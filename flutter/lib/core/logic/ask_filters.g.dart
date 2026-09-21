// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'ask_filters.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

_AskFilters _$AskFiltersFromJson(Map<String, dynamic> json) => _AskFilters(
  engine:
      $enumDecodeNullable(_$AnswerEngineEnumMap, json['engine']) ??
      AnswerEngine.ask,
  quartiles:
      (json['quartiles'] as List<dynamic>?)
          ?.map((e) => (e as num).toInt())
          .toList() ??
      const <int>[],
  keepUnranked: json['keep_unranked'] as bool? ?? false,
  yearMode:
      $enumDecodeNullable(_$YearModeEnumMap, json['year_mode']) ??
      YearMode.recent,
  years: (json['years'] as num?)?.toInt() ?? 3,
  yearFrom: (json['year_from'] as num?)?.toInt(),
  yearTo: (json['year_to'] as num?)?.toInt(),
  journals:
      (json['journals'] as List<dynamic>?)?.map((e) => e as String).toList() ??
      const <String>[],
  papers: (json['papers'] as num?)?.toInt() ?? 8,
  useKb: json['use_kb'] as bool? ?? true,
  kbHits: (json['kb_hits'] as num?)?.toInt() ?? 0,
  maxChars: (json['max_chars'] as num?)?.toInt() ?? 28000,
);

Map<String, dynamic> _$AskFiltersToJson(_AskFilters instance) =>
    <String, dynamic>{
      'engine': _$AnswerEngineEnumMap[instance.engine]!,
      'quartiles': instance.quartiles,
      'keep_unranked': instance.keepUnranked,
      'year_mode': _$YearModeEnumMap[instance.yearMode]!,
      'years': instance.years,
      'year_from': ?instance.yearFrom,
      'year_to': ?instance.yearTo,
      'journals': instance.journals,
      'papers': instance.papers,
      'use_kb': instance.useKb,
      'kb_hits': instance.kbHits,
      'max_chars': instance.maxChars,
    };

const _$AnswerEngineEnumMap = {
  AnswerEngine.ask: 'ask',
  AnswerEngine.codex: 'codex',
  AnswerEngine.interpret: 'interpret',
};

const _$YearModeEnumMap = {
  YearMode.any: 'any',
  YearMode.recent: 'recent',
  YearMode.range: 'range',
};
