import 'dart:convert';

import 'package:freezed_annotation/freezed_annotation.dart';

import '../api/sse.dart';
import '../models/tool_call.dart';

part 'job_live.freezed.dart';

enum StageKey {
  queries,
  search,
  fulltext,
  read,
  kb,
  synthesize,
  agent,
  reindex;

  /// 进度面板的阶段标题。
  String get label => switch (this) {
    StageKey.queries => '生成检索式',
    StageKey.search => '检索文献',
    StageKey.fulltext => '获取全文',
    StageKey.read => '逐篇阅读',
    StageKey.kb => '写入知识库',
    StageKey.synthesize => '综合成稿',
    StageKey.agent => '智能体检索与作答',
    StageKey.reindex => '重建索引',
  };

  /// 问答任务的阶段顺序。写库不在其中：API 侧 `defer_kb=True`，
  /// 原子事实抽取排到答案交付之后的独立后台任务，问答流水线里不会有 kb 事件
  /// （`reindex` 同理属于知识库任务）。
  static const askPipeline = <StageKey>[
    StageKey.queries,
    StageKey.search,
    StageKey.fulltext,
    StageKey.read,
    StageKey.synthesize,
  ];

  /// 文献解读的阶段顺序：解析上传 PDF → 生成综述或回答。
  /// 首轮开了知识库时，写库同样是答案交付后的独立后台任务。
  static const interpretPipeline = <StageKey>[
    StageKey.fulltext,
    StageKey.synthesize,
  ];

  static StageKey? parse(String? raw) {
    for (final stage in StageKey.values) {
      if (stage.name == raw) return stage;
    }
    return null;
  }
}

enum StageStatus { running, finished }

enum LogLevel { info, warning }

@freezed
abstract class StageState with _$StageState {
  const factory StageState({
    required StageStatus status,
    @Default(<String, dynamic>{}) Map<String, dynamic> detail,
  }) = _StageState;
}

@freezed
abstract class ProgressState with _$ProgressState {
  const factory ProgressState({
    required StageKey stage,
    @Default(0) int current,
    @Default(0) int total,
    String? title,
  }) = _ProgressState;
}

@freezed
abstract class LogLine with _$LogLine {
  const factory LogLine({
    @Default(LogLevel.info) LogLevel level,
    @Default('') String message,
  }) = _LogLine;
}

@freezed
abstract class CandidatePaper with _$CandidatePaper {
  const factory CandidatePaper({
    int? n,
    String? pmid,
    String? title,
    String? year,
    String? journal,
    String? rankLabel,
    String? pmcid,
  }) = _CandidatePaper;
}

@freezed
abstract class SearchSummary with _$SearchSummary {
  const factory SearchSummary({
    @Default(0) int candidates,
    @Default(0) int kept,
    @Default(<String, int>{}) Map<String, int> dropped,
    @Default(<CandidatePaper>[]) List<CandidatePaper> papers,
  }) = _SearchSummary;
}

/// 任务终态。
sealed class Terminal {
  const Terminal();
}

final class Succeeded extends Terminal {
  const Succeeded({this.answerId, this.items, this.papers});

  final String? answerId;
  final int? items;
  final int? papers;

  @override
  bool operator ==(Object other) =>
      other is Succeeded &&
      other.answerId == answerId &&
      other.items == items &&
      other.papers == papers;

  @override
  int get hashCode => Object.hash(answerId, items, papers);
}

final class Failed extends Terminal {
  const Failed({required this.code, required this.message});

  final String code;
  final String message;

  @override
  bool operator ==(Object other) =>
      other is Failed && other.code == code && other.message == message;

  @override
  int get hashCode => Object.hash(code, message);
}

final class Cancelled extends Terminal {
  const Cancelled();

  @override
  bool operator ==(Object other) => other is Cancelled;

  @override
  int get hashCode => 0x43414e;
}

/// 日志上限：超出后丢最早的若干条。
const logCapacity = 200;

/// SSE 事件归约出的实时状态。逐字移植 `frontend/src/lib/jobLive.ts`。
@freezed
abstract class JobLive with _$JobLive {
  const JobLive._();

  const factory JobLive({
    @Default(<StageKey, StageState>{}) Map<StageKey, StageState> stages,
    ProgressState? progress,
    @Default(<LogLine>[]) List<LogLine> logs,
    SearchSummary? search,
    @Default(<ToolCall>[]) List<ToolCall> tools,
    Terminal? terminal,
  }) = _JobLive;

  static const empty = JobLive();

  /// 单条事件归约。未知事件、字段缺失或类型不符时原样返回，终态不短路后续事件。
  JobLive applying(SseEvent event) {
    final data = _object(event.data);
    switch (event.event) {
      case 'stage':
        final stage = StageKey.parse(_string(data['stage']));
        final status = _string(data['status']);
        if (stage == null || (status != 'started' && status != 'finished')) {
          return this;
        }
        final detail = _objectValue(data['detail']);
        final next = {
          ...stages,
          stage: StageState(
            status: status == 'started'
                ? StageStatus.running
                : StageStatus.finished,
            detail: detail,
          ),
        };
        if (stage == StageKey.search && status == 'finished') {
          return copyWith(
            stages: next,
            search: SearchSummary(
              candidates: _count(detail['candidates']),
              kept: _count(detail['kept']),
              dropped: _objectValue(detail['dropped'])
                  .map((key, value) => MapEntry(key, _count(value))),
              papers: [...?(detail['papers'] as List?)?.map(_candidate)],
            ),
          );
        }
        return copyWith(stages: next);

      case 'progress':
        final stage = StageKey.parse(_string(data['stage']));
        if (stage == null) return this;
        return copyWith(
          progress: ProgressState(
            stage: stage,
            current: _count(data['current']),
            total: _count(data['total']),
            title: _string(data['title']),
          ),
        );

      case 'log':
        final message = _string(data['message']);
        if (message == null) return this;
        final next = [...logs];
        if (next.length >= logCapacity) {
          next.removeRange(0, next.length - (logCapacity - 1));
        }
        next.add(
          LogLine(
            level: _string(data['level']) == 'warning'
                ? LogLevel.warning
                : LogLevel.info,
            message: message,
          ),
        );
        return copyWith(logs: next);

      case 'tool':
        final callId = _string(data['call_id']);
        // 没有 call_id 就无法 upsert，宁可丢掉也不要在轨迹里堆重复行。
        if (callId == null) return this;
        final call = ToolCall(
          callId: callId,
          server: _string(data['server']) ?? '',
          tool: _string(data['tool']) ?? '',
          status: _toolStatus(_string(data['status'])),
          args: _objectValue(data['args']),
          durationMs: _int(data['duration_ms']),
          error: _string(data['error']),
        );
        final next = [...tools];
        final at = next.indexWhere((c) => c.callId == callId);
        if (at < 0) {
          next.add(call);
        } else {
          next[at] = call;
        }
        return copyWith(tools: next);

      case 'succeeded':
        return copyWith(
          terminal: Succeeded(
            answerId: _string(data['answer_id']),
            items: _int(data['items']),
            papers: _int(data['papers']),
          ),
        );

      case 'failed':
        return copyWith(
          terminal: Failed(
            code: _string(data['code']) ?? 'internal_error',
            message: _string(data['message']) ?? '',
          ),
        );

      case 'cancelled':
        return copyWith(terminal: const Cancelled());

      default:
        return this;
    }
  }
}

Map<String, dynamic> _object(String json) {
  try {
    final decoded = jsonDecode(json);
    return decoded is Map<String, dynamic> ? decoded : const {};
  } catch (_) {
    return const {};
  }
}

Map<String, dynamic> _objectValue(Object? raw) =>
    raw is Map<String, dynamic> ? raw : const {};

String? _string(Object? raw) => raw is String ? raw : null;

int? _int(Object? raw) => raw is num ? raw.toInt() : null;

int _count(Object? raw) => _int(raw) ?? 0;

/// 未知状态按「已结束」处理：转圈的行永远转下去比标错状态更糟。
ToolCallStatus _toolStatus(String? raw) => switch (raw) {
  'started' => ToolCallStatus.started,
  'failed' => ToolCallStatus.failed,
  _ => ToolCallStatus.completed,
};

CandidatePaper _candidate(Object? raw) {
  final value = _objectValue(raw);
  return CandidatePaper(
    n: _int(value['n']),
    pmid: _string(value['pmid']),
    title: _string(value['title']),
    year: _string(value['year']),
    journal: _string(value['journal']),
    rankLabel: _string(value['rank_label']),
    pmcid: _string(value['pmcid']),
  );
}

/// 阶段详情摘要文案，与 Web 端 `ProgressPipeline.summary()` 一致。
/// 说明：通用分支按键名排序输出（与 Apple 端一致，避免依赖 JSON 插入序）。
String stageSummary(StageKey stage, Map<String, dynamic> detail) {
  if (stage == StageKey.search) {
    final dropped = _objectValue(detail['dropped']);
    int at(String key) => _count(dropped[key]);
    return '候选 ${_count(detail['candidates'])} → 保留 ${_count(detail['kept'])}'
        '（年份 -${at('year')} / 分区 -${at('quartile')}'
        ' / 未收录 -${at('unranked')} / 期刊 -${at('journal')}）';
  }
  if (stage == StageKey.read) {
    return '相关 ${_count(detail['relevant'])}/${_count(detail['total'])}';
  }
  const labels = <String, String>{
    'queries': '检索式',
    'papers': '文献',
    'items': '条目',
    'total': '总计',
    'fulltext': '全文',
    'n_fulltext': '全文',
    'chars': '字符',
    'count': '数量',
    'facts': '事实',
  };
  final keys = detail.keys.toList()..sort();
  return keys
      .map((key) {
        final value = detail[key];
        if (value is! num) return null;
        final text = value == value.roundToDouble()
            ? '${value.toInt()}'
            : '$value';
        return '${labels[key] ?? key} $text';
      })
      .whereType<String>()
      .join(' · ');
}
