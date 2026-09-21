import 'package:freezed_annotation/freezed_annotation.dart';

import 'kb.dart';
import 'papers.dart';
import 'tool_call.dart';

part 'answers.freezed.dart';
part 'answers.g.dart';

enum AnswerStatus {
  queued,
  running,
  ready,
  failed,
  cancelled;

  /// 任务尚未进入终态（需要 SSE / 轮询继续跟踪）。
  bool get isActive =>
      this == AnswerStatus.queued || this == AnswerStatus.running;
}

enum AnswerEngine {
  ask,
  codex,
  interpret;

  bool get isCodex => this == AnswerEngine.codex;

  /// 智能体与文献解读都是多轮会话，答案就绪后输入框转为追问态。
  bool get supportsFollowUp =>
      this == AnswerEngine.codex || this == AnswerEngine.interpret;
}

/// 后端新增引擎时老客户端按「标准」渲染，而不是整份答案解不出来。
const _engineKey = JsonKey(unknownEnumValue: AnswerEngine.ask);

enum PaperSource {
  pmc,
  pdf,
  inst,
  upload,
  abstract;

  String get label => switch (this) {
    PaperSource.pmc => '全文 · PMC',
    PaperSource.pdf => '全文 · PDF',
    PaperSource.inst => '全文 · 机构',
    PaperSource.upload => '全文 · 上传',
    PaperSource.abstract => '仅摘要',
  };
}

/// 后端未来新增来源时不至于整份答案解码失败。
const _sourceKey = JsonKey(unknownEnumValue: PaperSource.abstract);

@freezed
abstract class JobError with _$JobError {
  const factory JobError({
    @Default('') String code,
    @Default('') String message,
  }) = _JobError;

  factory JobError.fromJson(Map<String, Object?> json) =>
      _$JobErrorFromJson(json);
}

/// 一篇被阅读的文献在答案中的元信息。注意 `year` 是字符串，未知字段一律为 `''`。
@freezed
abstract class AnswerPaper with _$AnswerPaper {
  const factory AnswerPaper({
    required int n,
    @Default('') String pmid,
    @Default('') String doi,
    @Default('') String pmcid,
    @Default('') String title,
    @Default('') String year,
    @Default('') String journal,
    @Default('') String issn,
    @Default('') String authors,
    @Default('') String quartile,
    @Default('') String rankLabel,
    @_sourceKey @Default(PaperSource.abstract) PaperSource source,
    int? relevance,
    @Default(0) int nParagraphs,
    @Default(0) int nCitations,
    @Default(0) int nCitationsVerified,
  }) = _AnswerPaper;

  factory AnswerPaper.fromJson(Map<String, Object?> json) =>
      _$AnswerPaperFromJson(json);
}

/// 答案正文引用到的一个原文段落。
@freezed
abstract class Citation with _$Citation {
  const factory Citation({
    required int n,
    @Default('') String pmid,
    @Default(0) int pid,
    @Default('') String sec,
    int? page,
    @Default('') String text,
    @Default(<String>[]) List<String> quotes,
    @Default(false) bool fromMarker,
  }) = _Citation;

  factory Citation.fromJson(Map<String, Object?> json) =>
      _$CitationFromJson(json);
}

@freezed
abstract class AnswerSummary with _$AnswerSummary {
  const factory AnswerSummary({
    required String id,
    String? jobId,
    required AnswerStatus status,
    @Default('') String question,
    @_engineKey @Default(AnswerEngine.ask) AnswerEngine engine,
    String? filtersLabel,
    int? nPapers,
    int? nFulltext,
    required DateTime createdAt,
    DateTime? finishedAt,
    JobError? error,
    @Default(1) int nTurns,
    String? rootQuestion,
  }) = _AnswerSummary;

  factory AnswerSummary.fromJson(Map<String, Object?> json) =>
      _$AnswerSummaryFromJson(json);
}

@freezed
abstract class Answer with _$Answer {
  const factory Answer({
    required String id,
    String? jobId,
    required AnswerStatus status,
    @Default('') String question,
    @_engineKey @Default(AnswerEngine.ask) AnswerEngine engine,
    String? filtersLabel,
    int? nPapers,
    int? nFulltext,
    required DateTime createdAt,
    DateTime? finishedAt,
    JobError? error,
    @Default(1) int nTurns,
    String? rootQuestion,
    String? parentId,
    String? questionEn,
    @Default(<String>[]) List<String> queries,
    @Default(<String, dynamic>{}) Map<String, dynamic> options,
    DateTime? startedAt,
    @Default(<AnswerPaper>[]) List<AnswerPaper> papers,
    String? bodyMd,
    @Default(<Citation>[]) List<Citation> citations,
    @Default(<KbHit>[]) List<KbHit> kbHits,
    @Default(<ToolCall>[]) List<ToolCall> trace,
  }) = _Answer;

  factory Answer.fromJson(Map<String, Object?> json) => _$AnswerFromJson(json);
}

/// 逐篇阅读材料：在 [AnswerPaper] 之上追加笔记、核实引文、事实与全文。
@freezed
abstract class AnswerPaperDetail with _$AnswerPaperDetail {
  const factory AnswerPaperDetail({
    required int n,
    @Default('') String pmid,
    @Default('') String doi,
    @Default('') String pmcid,
    @Default('') String title,
    @Default('') String year,
    @Default('') String journal,
    @Default('') String issn,
    @Default('') String authors,
    @Default('') String quartile,
    @Default('') String rankLabel,
    @_sourceKey @Default(PaperSource.abstract) PaperSource source,
    int? relevance,
    @Default(0) int nParagraphs,
    @Default(0) int nCitations,
    @Default(0) int nCitationsVerified,
    @Default('') String notesMd,
    @Default(<VerifiedQuote>[]) List<VerifiedQuote> citations,
    @Default(<Fact>[]) List<Fact> facts,
    @Default(<Paragraph>[]) List<Paragraph> paragraphs,
    @Default('') String fulltextMd,
  }) = _AnswerPaperDetail;

  factory AnswerPaperDetail.fromJson(Map<String, Object?> json) =>
      _$AnswerPaperDetailFromJson(json);
}

/// 创建问答任务的请求体。可选字段为 null 时不编码（后端对 years / year_from 互斥有校验）。
@freezed
abstract class AnswerCreate with _$AnswerCreate {
  const factory AnswerCreate({
    required String question,
    @Default(AnswerEngine.ask) AnswerEngine engine,
    required int papers,
    int? years,
    int? yearFrom,
    int? yearTo,
    @Default(<int>[]) List<int> quartiles,
    @Default(<String>[]) List<String> journals,
    bool? keepUnranked,
    @Default(true) bool useKb,
    // codex 引擎不走确定性流水线：这两项传 null 即不编码，
    // 免得让人以为「字符预算 / 知识库命中」还在生效。
    int? kbHits,
    int? maxChars,
  }) = _AnswerCreate;

  factory AnswerCreate.fromJson(Map<String, Object?> json) =>
      _$AnswerCreateFromJson(json);
}

/// 在已有智能体会话上追问；其余选项一律沿用被追问的那一轮。
@freezed
abstract class FollowupCreate with _$FollowupCreate {
  const factory FollowupCreate({required String question}) = _FollowupCreate;

  factory FollowupCreate.fromJson(Map<String, Object?> json) =>
      _$FollowupCreateFromJson(json);
}
