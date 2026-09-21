// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'jobs.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

_JobProgress _$JobProgressFromJson(Map<String, dynamic> json) => _JobProgress(
  stage: json['stage'] as String? ?? '',
  current: (json['current'] as num?)?.toInt(),
  total: (json['total'] as num?)?.toInt(),
);

Map<String, dynamic> _$JobProgressToJson(_JobProgress instance) =>
    <String, dynamic>{
      'stage': instance.stage,
      'current': ?instance.current,
      'total': ?instance.total,
    };

_Job _$JobFromJson(Map<String, dynamic> json) => _Job(
  id: json['id'] as String,
  kind: $enumDecode(_$JobKindEnumMap, json['kind']),
  status: $enumDecode(_$JobStatusEnumMap, json['status']),
  userId: json['user_id'] as String?,
  params: json['params'] as Map<String, dynamic>? ?? const <String, dynamic>{},
  progress: json['progress'] == null
      ? null
      : JobProgress.fromJson(json['progress'] as Map<String, dynamic>),
  error: json['error'] == null
      ? null
      : JobError.fromJson(json['error'] as Map<String, dynamic>),
  result: json['result'] as Map<String, dynamic>?,
  createdAt: DateTime.parse(json['created_at'] as String),
  startedAt: json['started_at'] == null
      ? null
      : DateTime.parse(json['started_at'] as String),
  finishedAt: json['finished_at'] == null
      ? null
      : DateTime.parse(json['finished_at'] as String),
);

Map<String, dynamic> _$JobToJson(_Job instance) => <String, dynamic>{
  'id': instance.id,
  'kind': _$JobKindEnumMap[instance.kind]!,
  'status': _$JobStatusEnumMap[instance.status]!,
  'user_id': ?instance.userId,
  'params': instance.params,
  'progress': ?instance.progress?.toJson(),
  'error': ?instance.error?.toJson(),
  'result': ?instance.result,
  'created_at': instance.createdAt.toIso8601String(),
  'started_at': ?instance.startedAt?.toIso8601String(),
  'finished_at': ?instance.finishedAt?.toIso8601String(),
};

const _$JobKindEnumMap = {
  JobKind.ask: 'ask',
  JobKind.codex: 'codex',
  JobKind.interpret: 'interpret',
  JobKind.kbReindex: 'kb_reindex',
  JobKind.paperIngest: 'paper_ingest',
  JobKind.answerKb: 'answer_kb',
};

const _$JobStatusEnumMap = {
  JobStatus.queued: 'queued',
  JobStatus.running: 'running',
  JobStatus.succeeded: 'succeeded',
  JobStatus.failed: 'failed',
  JobStatus.cancelled: 'cancelled',
};
