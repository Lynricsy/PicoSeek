import 'package:flutter/material.dart';

import '../../app/theme/tokens.dart';
import '../../core/logic/ask_rail.dart';
import '../../core/logic/job_live.dart';
import '../../core/models/answers.dart';
import '../../shared/widgets/badges.dart';
import '../../shared/widgets/surface.dart';
import 'job_live_monitor.dart';
import 'stage_rail.dart';
import 'trace_list.dart';

/// 任务进度。标准引擎是阶段流水线 + 当前阶段进度 + 检索摘要；
/// 智能体引擎没有固定阶段，改用一行状态 + 检索轨迹。运行日志刻意不展示。
/// 文献解读走流水线分支，只是阶段更少（由 [ProgressPipeline.steps] 决定）。
class ProgressPipeline extends StatelessWidget {
  const ProgressPipeline({
    super.key,
    required this.state,
    this.engine = AnswerEngine.ask,
    required this.useKb,
    required this.onCandidateTap,
    this.onCancel,
    this.cancelRequested = false,
    this.steps = StageKey.askPipeline,
    this.title,
  });

  final JobLiveState state;
  final AnswerEngine engine;

  /// 是否开启知识库：关掉时流水线末尾不出现「写入知识库」节点。
  final bool useKb;

  /// 点击候选文献卡（进行中时材料可能还没生成）。
  final void Function(String pmid) onCandidateTap;

  /// 取消任务；为 null 时不显示取消入口（除非 [cancelRequested]）。
  final VoidCallback? onCancel;
  final bool cancelRequested;

  /// 阶段序列：文献解读传 [StageKey.interpretPipeline]。
  final List<StageKey> steps;

  /// 面板标题；为 null 时按引擎取默认文案。
  final String? title;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final live = state.live;
    final progress = live.progress;

    final codex = engine == AnswerEngine.codex;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                title ?? (codex ? '智能体检索' : '证据流水线'),
                style: theme.textTheme.labelLarge,
              ),
            ),
            if (onCancel != null || cancelRequested)
              TextButton.icon(
                onPressed: onCancel,
                icon: const Icon(Icons.stop_circle_outlined, size: 16),
                label: Text(cancelRequested ? '正在取消…' : '取消'),
              ),
          ],
        ),
        const SizedBox(height: PicoSeekTokens.space3),
        if (codex)
          // 轨迹本身就是进展，不用文字复述系统在干什么；
          // 还没有轨迹时只给一个转圈表示在跑。
          live.tools.isEmpty
              ? const SizedBox.square(
                  dimension: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : TraceList(calls: live.tools, live: true)
        else ...[
          // 写库不在这条流水线里：答案交付后才排后台任务，
          // 所以此刻它只是末尾一个「未开始」的节点。
          StageRail(
            nodes: askRailNodes(live, useKb: useKb, steps: steps),
            progress: progress == null
                ? null
                : (
                    label: progress.stage.label,
                    current: progress.current,
                    total: progress.total,
                    detail: progress.title,
                  ),
          ),
          if (live.search != null) ...[
            const SizedBox(height: PicoSeekTokens.space4),
            _SearchSummaryView(
              summary: live.search!,
              detail: live.stages[StageKey.search]?.detail ?? const {},
              onCandidateTap: onCandidateTap,
            ),
          ],
        ],
      ],
    );
  }
}

class _SearchSummaryView extends StatelessWidget {
  const _SearchSummaryView({
    required this.summary,
    required this.detail,
    required this.onCandidateTap,
  });

  final SearchSummary summary;
  final Map<String, dynamic> detail;
  final void Function(String pmid) onCandidateTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          stageSummary(StageKey.search, detail),
          style: theme.textTheme.labelMedium?.copyWith(
            color: theme.colorScheme.onSurfaceVariant,
          ),
        ),
        if (summary.papers.isNotEmpty) ...[
          const SizedBox(height: PicoSeekTokens.space2),
          Wrap(
            spacing: PicoSeekTokens.space2,
            runSpacing: PicoSeekTokens.space2,
            children: [
              for (final paper in summary.papers)
                _CandidateCard(paper: paper, onTap: onCandidateTap),
            ],
          ),
        ],
      ],
    );
  }
}

class _CandidateCard extends StatelessWidget {
  const _CandidateCard({required this.paper, required this.onTap});

  final CandidatePaper paper;
  final void Function(String pmid) onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final pmid = paper.pmid ?? '';
    return ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 320),
      child: PicoSeekCard(
        onTap: pmid.isEmpty ? null : () => onTap(pmid),
        padding: const EdgeInsets.all(PicoSeekTokens.space3),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                if (paper.n != null) ...[
                  CitationSquare(n: paper.n!, size: 18),
                  const SizedBox(width: PicoSeekTokens.space2),
                ],
                Expanded(
                  child: Text(
                    paper.title ?? '（无标题）',
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.labelLarge,
                  ),
                ),
              ],
            ),
            const SizedBox(height: PicoSeekTokens.space1),
            Text(
              [
                paper.journal ?? '',
                paper.year ?? '',
                paper.rankLabel ?? '',
              ].where((part) => part.isNotEmpty).join(' · '),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.labelSmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
