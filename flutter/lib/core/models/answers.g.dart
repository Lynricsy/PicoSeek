// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'answers.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

_JobError _$JobErrorFromJson(Map<String, dynamic> json) => _JobError(
  code: json['code'] as String? ?? '',
  message: json['message'] as String? ?? '',
);

Map<String, dynamic> _$JobErrorToJson(_JobError instance) => <String, dynamic>{
  'code': instance.code,
  'message': instance.message,
};

_AnswerPaper _$AnswerPaperFromJson(Map<String, dynamic> json) => _AnswerPaper(
  n: (json['n'] as num).toInt(),
  pmid: json['pmid'] as String? ?? '',
  doi: json['doi'] as String? ?? '',
  pmcid: json['pmcid'] as String? ?? '',
  title: json['title'] as String? ?? '',
  year: json['year'] as String? ?? '',
  journal: json['journal'] as String? ?? '',
  issn: json['issn'] as String? ?? '',
  authors: json['authors'] as String? ?? '',
  quartile: json['quartile'] as String? ?? '',
  rankLabel: json['rank_label'] as String? ?? '',
  source:
      $enumDecodeNullable(
        _$PaperSourceEnumMap,
        json['source'],
        unknownValue: PaperSource.abstract,
      ) ??
      PaperSource.abstract,
  relevance: (json['relevance'] as num?)?.toInt(),
  nParagraphs: (json['n_paragraphs'] as num?)?.toInt() ?? 0,
  nCitations: (json['n_citations'] as num?)?.toInt() ?? 0,
  nCitationsVerified: (json['n_citations_verified'] as num?)?.toInt() ?? 0,
);

Map<String, dynamic> _$AnswerPaperToJson(_AnswerPaper instance) =>
    <String, dynamic>{
      'n': instance.n,
      'pmid': instance.pmid,
      'doi': instance.doi,
      'pmcid': instance.pmcid,
      'title': instance.title,
      'year': instance.year,
      'journal': instance.journal,
      'issn': instance.issn,
      'authors': instance.authors,
      'quartile': instance.quartile,
      'rank_label': instance.rankLabel,
      'source': _$PaperSourceEnumMap[instance.source]!,
      'relevance': ?instance.relevance,
      'n_paragraphs': instance.nParagraphs,
      'n_citations': instance.nCitations,
      'n_citations_verified': instance.nCitationsVerified,
    };

const _$PaperSourceEnumMap = {
  PaperSource.pmc: 'pmc',
  PaperSource.pdf: 'pdf',
  PaperSource.inst: 'inst',
  PaperSource.upload: 'upload',
  PaperSource.abstract: 'abstract',
};

_Citation _$CitationFromJson(Map<String, dynamic> json) => _Citation(
  n: (json['n'] as num).toInt(),
  pmid: json['pmid'] as String? ?? '',
  pid: (json['pid'] as num?)?.toInt() ?? 0,
  sec: json['sec'] as String? ?? '',
  page: (json['page'] as num?)?.toInt(),
  text: json['text'] as String? ?? '',
  quotes:
      (json['quotes'] as List<dynamic>?)?.map((e) => e as String).toList() ??
      const <String>[],
  fromMarker: json['from_marker'] as bool? ?? false,
);

Map<String, dynamic> _$CitationToJson(_Citation instance) => <String, dynamic>{
  'n': instance.n,
  'pmid': instance.pmid,
  'pid': instance.pid,
  'sec': instance.sec,
  'page': ?instance.page,
  'text': instance.text,
  'quotes': instance.quotes,
  'from_marker': instance.fromMarker,
};

_AnswerSummary _$AnswerSummaryFromJson(Map<String, dynamic> json) =>
    _AnswerSummary(
      id: json['id'] as String,
      jobId: json['job_id'] as String?,
      status: $enumDecode(_$AnswerStatusEnumMap, json['status']),
      question: json['question'] as String? ?? '',
      engine:
          $enumDecodeNullable(
            _$AnswerEngineEnumMap,
            json['engine'],
            unknownValue: AnswerEngine.ask,
          ) ??
          AnswerEngine.ask,
      filtersLabel: json['filters_label'] as String?,
      nPapers: (json['n_papers'] as num?)?.toInt(),
      nFulltext: (json['n_fulltext'] as num?)?.toInt(),
      createdAt: DateTime.parse(json['created_at'] as String),
      finishedAt: json['finished_at'] == null
          ? null
          : DateTime.parse(json['finished_at'] as String),
      error: json['error'] == null
          ? null
          : JobError.fromJson(json['error'] as Map<String, dynamic>),
      nTurns: (json['n_turns'] as num?)?.toInt() ?? 1,
      rootQuestion: json['root_question'] as String?,
    );

Map<String, dynamic> _$AnswerSummaryToJson(_AnswerSummary instance) =>
    <String, dynamic>{
      'id': instance.id,
      'job_id': ?instance.jobId,
      'status': _$AnswerStatusEnumMap[instance.status]!,
      'question': instance.question,
      'engine': _$AnswerEngineEnumMap[instance.engine]!,
      'filters_label': ?instance.filtersLabel,
      'n_papers': ?instance.nPapers,
      'n_fulltext': ?instance.nFulltext,
      'created_at': instance.createdAt.toIso8601String(),
      'finished_at': ?instance.finishedAt?.toIso8601String(),
      'error': ?instance.error?.toJson(),
      'n_turns': instance.nTurns,
      'root_question': ?instance.rootQuestion,
    };

const _$AnswerStatusEnumMap = {
  AnswerStatus.queued: 'queued',
  AnswerStatus.running: 'running',
  AnswerStatus.ready: 'ready',
  AnswerStatus.failed: 'failed',
  AnswerStatus.cancelled: 'cancelled',
};

const _$AnswerEngineEnumMap = {
  AnswerEngine.ask: 'ask',
  AnswerEngine.codex: 'codex',
  AnswerEngine.interpret: 'interpret',
};

_Answer _$AnswerFromJson(Map<String, dynamic> json) => _Answer(
  id: json['id'] as String,
  jobId: json['job_id'] as String?,
  status: $enumDecode(_$AnswerStatusEnumMap, json['status']),
  question: json['question'] as String? ?? '',
  engine:
      $enumDecodeNullable(
        _$AnswerEngineEnumMap,
        json['engine'],
        unknownValue: AnswerEngine.ask,
      ) ??
      AnswerEngine.ask,
  filtersLabel: json['filters_label'] as String?,
  nPapers: (json['n_papers'] as num?)?.toInt(),
  nFulltext: (json['n_fulltext'] as num?)?.toInt(),
  createdAt: DateTime.parse(json['created_at'] as String),
  finishedAt: json['finished_at'] == null
      ? null
      : DateTime.parse(json['finished_at'] as String),
  error: json['error'] == null
      ? null
      : JobError.fromJson(json['error'] as Map<String, dynamic>),
  nTurns: (json['n_turns'] as num?)?.toInt() ?? 1,
  rootQuestion: json['root_question'] as String?,
  parentId: json['parent_id'] as String?,
  questionEn: json['question_en'] as String?,
  queries:
      (json['queries'] as List<dynamic>?)?.map((e) => e as String).toList() ??
      const <String>[],
  options:
      json['options'] as Map<String, dynamic>? ?? const <String, dynamic>{},
  startedAt: json['started_at'] == null
      ? null
      : DateTime.parse(json['started_at'] as String),
  papers:
      (json['papers'] as List<dynamic>?)
          ?.map((e) => AnswerPaper.fromJson(e as Map<String, dynamic>))
          .toList() ??
      const <AnswerPaper>[],
  bodyMd: json['body_md'] as String?,
  citations:
      (json['citations'] as List<dynamic>?)
          ?.map((e) => Citation.fromJson(e as Map<String, dynamic>))
          .toList() ??
      const <Citation>[],
  kbHits:
      (json['kb_hits'] as List<dynamic>?)
          ?.map((e) => KbHit.fromJson(e as Map<String, dynamic>))
          .toList() ??
      const <KbHit>[],
  trace:
      (json['trace'] as List<dynamic>?)
          ?.map((e) => ToolCall.fromJson(e as Map<String, dynamic>))
          .toList() ??
      const <ToolCall>[],
);

Map<String, dynamic> _$AnswerToJson(_Answer instance) => <String, dynamic>{
  'id': instance.id,
  'job_id': ?instance.jobId,
  'status': _$AnswerStatusEnumMap[instance.status]!,
  'question': instance.question,
  'engine': _$AnswerEngineEnumMap[instance.engine]!,
  'filters_label': ?instance.filtersLabel,
  'n_papers': ?instance.nPapers,
  'n_fulltext': ?instance.nFulltext,
  'created_at': instance.createdAt.toIso8601String(),
  'finished_at': ?instance.finishedAt?.toIso8601String(),
  'error': ?instance.error?.toJson(),
  'n_turns': instance.nTurns,
  'root_question': ?instance.rootQuestion,
  'parent_id': ?instance.parentId,
  'question_en': ?instance.questionEn,
  'queries': instance.queries,
  'options': instance.options,
  'started_at': ?instance.startedAt?.toIso8601String(),
  'papers': instance.papers.map((e) => e.toJson()).toList(),
  'body_md': ?instance.bodyMd,
  'citations': instance.citations.map((e) => e.toJson()).toList(),
  'kb_hits': instance.kbHits.map((e) => e.toJson()).toList(),
  'trace': instance.trace.map((e) => e.toJson()).toList(),
};

_AnswerPaperDetail _$AnswerPaperDetailFromJson(Map<String, dynamic> json) =>
    _AnswerPaperDetail(
      n: (json['n'] as num).toInt(),
      pmid: json['pmid'] as String? ?? '',
      doi: json['doi'] as String? ?? '',
      pmcid: json['pmcid'] as String? ?? '',
      title: json['title'] as String? ?? '',
      year: json['year'] as String? ?? '',
      journal: json['journal'] as String? ?? '',
      issn: json['issn'] as String? ?? '',
      authors: json['authors'] as String? ?? '',
      quartile: json['quartile'] as String? ?? '',
      rankLabel: json['rank_label'] as String? ?? '',
      source:
          $enumDecodeNullable(
            _$PaperSourceEnumMap,
            json['source'],
            unknownValue: PaperSource.abstract,
          ) ??
          PaperSource.abstract,
      relevance: (json['relevance'] as num?)?.toInt(),
      nParagraphs: (json['n_paragraphs'] as num?)?.toInt() ?? 0,
      nCitations: (json['n_citations'] as num?)?.toInt() ?? 0,
      nCitationsVerified: (json['n_citations_verified'] as num?)?.toInt() ?? 0,
      notesMd: json['notes_md'] as String? ?? '',
      citations:
          (json['citations'] as List<dynamic>?)
              ?.map((e) => VerifiedQuote.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const <VerifiedQuote>[],
      facts:
          (json['facts'] as List<dynamic>?)
              ?.map((e) => Fact.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const <Fact>[],
      paragraphs:
          (json['paragraphs'] as List<dynamic>?)
              ?.map((e) => Paragraph.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const <Paragraph>[],
      fulltextMd: json['fulltext_md'] as String? ?? '',
    );

Map<String, dynamic> _$AnswerPaperDetailToJson(_AnswerPaperDetail instance) =>
    <String, dynamic>{
      'n': instance.n,
      'pmid': instance.pmid,
      'doi': instance.doi,
      'pmcid': instance.pmcid,
      'title': instance.title,
      'year': instance.year,
      'journal': instance.journal,
      'issn': instance.issn,
      'authors': instance.authors,
      'quartile': instance.quartile,
      'rank_label': instance.rankLabel,
      'source': _$PaperSourceEnumMap[instance.source]!,
      'relevance': ?instance.relevance,
      'n_paragraphs': instance.nParagraphs,
      'n_citations': instance.nCitations,
      'n_citations_verified': instance.nCitationsVerified,
      'notes_md': instance.notesMd,
      'citations': instance.citations.map((e) => e.toJson()).toList(),
      'facts': instance.facts.map((e) => e.toJson()).toList(),
      'paragraphs': instance.paragraphs.map((e) => e.toJson()).toList(),
      'fulltext_md': instance.fulltextMd,
    };

_AnswerCreate _$AnswerCreateFromJson(Map<String, dynamic> json) =>
    _AnswerCreate(
      question: json['question'] as String,
      engine:
          $enumDecodeNullable(_$AnswerEngineEnumMap, json['engine']) ??
          AnswerEngine.ask,
      papers: (json['papers'] as num).toInt(),
      years: (json['years'] as num?)?.toInt(),
      yearFrom: (json['year_from'] as num?)?.toInt(),
      yearTo: (json['year_to'] as num?)?.toInt(),
      quartiles:
          (json['quartiles'] as List<dynamic>?)
              ?.map((e) => (e as num).toInt())
              .toList() ??
          const <int>[],
      journals:
          (json['journals'] as List<dynamic>?)
              ?.map((e) => e as String)
              .toList() ??
          const <String>[],
      keepUnranked: json['keep_unranked'] as bool?,
      useKb: json['use_kb'] as bool? ?? true,
      kbHits: (json['kb_hits'] as num?)?.toInt(),
      maxChars: (json['max_chars'] as num?)?.toInt(),
    );

Map<String, dynamic> _$AnswerCreateToJson(_AnswerCreate instance) =>
    <String, dynamic>{
      'question': instance.question,
      'engine': _$AnswerEngineEnumMap[instance.engine]!,
      'papers': instance.papers,
      'years': ?instance.years,
      'year_from': ?instance.yearFrom,
      'year_to': ?instance.yearTo,
      'quartiles': instance.quartiles,
      'journals': instance.journals,
      'keep_unranked': ?instance.keepUnranked,
      'use_kb': instance.useKb,
      'kb_hits': ?instance.kbHits,
      'max_chars': ?instance.maxChars,
    };

_FollowupCreate _$FollowupCreateFromJson(Map<String, dynamic> json) =>
    _FollowupCreate(question: json['question'] as String);

Map<String, dynamic> _$FollowupCreateToJson(_FollowupCreate instance) =>
    <String, dynamic>{'question': instance.question};
