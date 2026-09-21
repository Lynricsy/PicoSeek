import 'package:freezed_annotation/freezed_annotation.dart';

import '../models/jobs.dart';
import 'job_live.dart';

part 'ask_rail.freezed.dart';

/// 节点状态：`waiting` 是「排上队但还没轮到」，与还没走到的 `todo` 不是一回事。
enum RailStatus { todo, waiting, running, done, failed, cancelled }

/// 后台写库任务的状态；`unknown` 表示状态读不到，而不是没有任务。
enum KbStatus {
  queued,
  running,
  succeeded,
  failed,
  cancelled,
  unknown;

  static KbStatus of(JobStatus status) => switch (status) {
    JobStatus.queued => KbStatus.queued,
    JobStatus.running => KbStatus.running,
    JobStatus.succeeded => KbStatus.succeeded,
    JobStatus.failed => KbStatus.failed,
    JobStatus.cancelled => KbStatus.cancelled,
  };

  bool get isActive => this == KbStatus.queued || this == KbStatus.running;
}

/// 答案交付后才排队的写库任务。
@freezed
abstract class BackgroundKb with _$BackgroundKb {
  const factory BackgroundKb({
    required KbStatus status,
    @Default(0) int current,
    @Default(0) int total,
  }) = _BackgroundKb;

  /// 请求失败或状态读不到时的占位。
  static const unknown = BackgroundKb(status: KbStatus.unknown);
}

/// 阶段节点条的一格。[hint] 是节点下的一行小字，承载「（1/10 篇）」这类计数与原因。
@freezed
abstract class AskRailNode with _$AskRailNode {
  const factory AskRailNode({
    required String key,
    required String label,
    required RailStatus status,
    String? hint,
  }) = _AskRailNode;
}

AskRailNode _kbNode(BackgroundKb? kb) {
  final label = StageKey.kb.label;
  AskRailNode node(RailStatus status, String hint) => AskRailNode(
    key: StageKey.kb.name,
    label: label,
    status: status,
    hint: hint,
  );
  if (kb == null) return node(RailStatus.todo, '答案交付后在后台进行');
  final count = kb.total > 0 ? '（${kb.current}/${kb.total} 篇）' : '';
  return switch (kb.status) {
    KbStatus.queued => node(RailStatus.waiting, '等待后台，优先执行新问答$count'),
    KbStatus.running => node(RailStatus.running, '后台写入中$count'),
    KbStatus.succeeded => node(
      RailStatus.done,
      kb.total > 0 ? '已写入 ${kb.total} 篇' : '已写入',
    ),
    KbStatus.failed => node(RailStatus.failed, '写入失败，答案不受影响'),
    KbStatus.cancelled => node(RailStatus.cancelled, '已取消，答案不受影响'),
    KbStatus.unknown => node(RailStatus.todo, '状态暂时无法读取'),
  };
}

/// 答案页与运行页共用的节点序列。逐字对应 `frontend/src/components/ask/askRail.ts`。
///
/// 写库不在问答流水线里跑：API 侧 `run_ask(..., defer_kb=True)`，答案交付之后
/// worker 才另建 `answer_kb` 后台任务，所以它是「综合成稿」之后的节点，
/// 状态只能来自那个子任务，问答的 SSE 里永远不会有 kb 阶段事件。
///
/// [settled] 表示答案已出：重新进入答案页时 SSE 状态是空的，
/// 前序阶段一律按已完成渲染，否则节点会全灰。
///
/// [steps] 是阶段序列，默认问答流水线；文献解读传 [StageKey.interpretPipeline]。
List<AskRailNode> askRailNodes(
  JobLive live, {
  required bool useKb,
  BackgroundKb? kb,
  bool settled = false,
  List<StageKey> steps = StageKey.askPipeline,
}) {
  final nodes = <AskRailNode>[
    for (final stage in steps) _stageNode(live, stage, settled),
  ];
  return useKb ? [...nodes, _kbNode(kb)] : nodes;
}

AskRailNode _stageNode(JobLive live, StageKey stage, bool settled) {
  final state = live.stages[stage];
  final finished = state?.status == StageStatus.finished;
  // 已完成的阶段把详情摘要（「相关 4/6」之类）挂在节点小字上。
  final summary = finished ? stageSummary(stage, state!.detail) : '';
  return AskRailNode(
    key: stage.name,
    label: stage.label,
    status: finished || settled
        ? RailStatus.done
        : (state != null ? RailStatus.running : RailStatus.todo),
    hint: summary.isEmpty ? null : summary,
  );
}
