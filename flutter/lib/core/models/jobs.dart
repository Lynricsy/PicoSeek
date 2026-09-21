import 'package:freezed_annotation/freezed_annotation.dart';

import 'answers.dart';

part 'jobs.freezed.dart';
part 'jobs.g.dart';

enum JobKind {
  ask,
  codex,
  interpret,
  @JsonValue('kb_reindex')
  kbReindex,
  @JsonValue('paper_ingest')
  paperIngest,
  @JsonValue('answer_kb')
  answerKb,
}

enum JobStatus {
  queued,
  running,
  succeeded,
  failed,
  cancelled;

  bool get isActive => this == JobStatus.queued || this == JobStatus.running;
}

@freezed
abstract class JobProgress with _$JobProgress {
  const factory JobProgress({
    @Default('') String stage,
    int? current,
    int? total,
  }) = _JobProgress;

  factory JobProgress.fromJson(Map<String, Object?> json) =>
      _$JobProgressFromJson(json);
}

@freezed
abstract class Job with _$Job {
  const factory Job({
    required String id,
    required JobKind kind,
    required JobStatus status,
    String? userId,
    @Default(<String, dynamic>{}) Map<String, dynamic> params,
    JobProgress? progress,
    JobError? error,
    Map<String, dynamic>? result,
    required DateTime createdAt,
    DateTime? startedAt,
    DateTime? finishedAt,
  }) = _Job;

  factory Job.fromJson(Map<String, Object?> json) => _$JobFromJson(json);
}
