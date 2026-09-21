import 'package:flutter_test/flutter_test.dart';
import 'package:picoseek/core/models/answers.dart';
import 'package:picoseek/core/models/jobs.dart';
import 'package:picoseek/core/models/literature.dart';
import 'package:picoseek/core/models/meta.dart';
import 'package:picoseek/core/models/page.dart';
import 'package:picoseek/core/models/problem.dart';
import 'package:picoseek/core/models/tool_call.dart';

void main() {
  test('snake_case + 默认值 + 未知来源回落', () {
    final answer = Answer.fromJson({
      'id': 'a1',
      'job_id': 'j1',
      'status': 'ready',
      'question': 'q',
      'filters_label': 'lbl',
      'created_at': '2026-09-06T14:20:57Z',
      'papers': [
        {'n': 1, 'source': 'brand_new', 'n_citations_verified': 2},
      ],
    });
    expect(answer.jobId, 'j1');
    expect(answer.status, AnswerStatus.ready);
    expect(answer.createdAt.toUtc().year, 2026);
    expect(answer.papers.single.source, PaperSource.abstract);
    expect(answer.papers.single.nCitationsVerified, 2);
    expect(answer.options, isEmpty);
    expect(answer.queries, isEmpty);
  });

  test('AnswerCreate 省略 null 字段', () {
    final json = const AnswerCreate(
      question: 'q',
      papers: 8,
      years: 3,
    ).toJson();
    expect(json.containsKey('year_from'), isFalse);
    expect(json.containsKey('keep_unranked'), isFalse);
    expect(json.containsKey('max_chars'), isFalse);
    expect(json.containsKey('kb_hits'), isFalse);
    expect(json['use_kb'], isTrue);
    expect(json['engine'], 'ask');
  });

  test('codex 引擎与检索轨迹的 snake_case 映射', () {
    final answer = Answer.fromJson({
      'id': 'a2',
      'status': 'ready',
      'question': '追问',
      'engine': 'codex',
      'created_at': '2026-09-06T14:20:57Z',
      'parent_id': 'a1',
      'n_turns': 2,
      'root_question': '根问题',
      'trace': [
        {
          'call_id': 'c1',
          'server': 'semantic_scholar',
          'tool': 'read_pdf',
          'status': 'completed',
          'args': {'path': 'x.pdf'},
          'duration_ms': 820,
        },
      ],
    });
    expect(answer.engine, AnswerEngine.codex);
    expect(answer.parentId, 'a1');
    expect(answer.nTurns, 2);
    expect(answer.rootQuestion, '根问题');
    final call = answer.trace.single;
    expect(call.callId, 'c1');
    expect(call.server, 'semantic_scholar');
    expect(call.tool, 'read_pdf');
    expect(call.status, ToolCallStatus.completed);
    expect(call.args['path'], 'x.pdf');
    expect(call.durationMs, 820);
    expect(call.error, isNull);
  });

  test('未知引擎回落标准，JobKind codex 可解', () {
    final summary = AnswerSummary.fromJson({
      'id': 'a3',
      'status': 'queued',
      'question': 'q',
      'engine': 'brand_new',
      'created_at': '2026-09-06T14:20:57Z',
    });
    expect(summary.engine, AnswerEngine.ask);
    expect(summary.nTurns, 1);

    final job = Job.fromJson({
      'id': 'j',
      'kind': 'codex',
      'status': 'running',
      'created_at': '2026-09-06T14:20:57Z',
    });
    expect(job.kind, JobKind.codex);
  });

  test('JobKind kb_reindex 与 Page 泛型', () {
    final job = Job.fromJson({
      'id': 'j',
      'kind': 'kb_reindex',
      'status': 'running',
      'created_at': '2026-09-06T14:20:57Z',
    });
    expect(job.kind, JobKind.kbReindex);
    final page = Page<AnswerSummary>.fromJson({
      'items': [
        {
          'id': 'a',
          'status': 'queued',
          'created_at': '2026-09-06T14:20:57Z',
        },
      ],
      'total': 1,
      'limit': 20,
      'offset': 0,
    }, (o) => AnswerSummary.fromJson(o! as Map<String, Object?>));
    expect(page.items.single.status, AnswerStatus.queued);
  });

  test('interpret 引擎与 JobKind interpret 往返', () {
    final answer = Answer.fromJson({
      'id': 'a4',
      'status': 'ready',
      'question': '梳理这组文献的组织关系与差异',
      'engine': 'interpret',
      'created_at': '2026-09-06T14:20:57Z',
      'options': {'mode': 'overview', 'n_files': 2, 'use_kb': true},
      'papers': [
        {'n': 1, 'title': '上传的论文', 'source': 'upload'},
      ],
    });
    expect(answer.engine, AnswerEngine.interpret);
    expect(answer.engine.supportsFollowUp, isTrue);
    expect(answer.papers.single.source, PaperSource.upload);
    expect(answer.options['mode'], 'overview');

    final job = Job.fromJson({
      'id': 'j',
      'kind': 'interpret',
      'status': 'running',
      'created_at': '2026-09-06T14:20:57Z',
    });
    expect(job.kind, JobKind.interpret);
    expect(job.toJson()['kind'], 'interpret');
  });

  test('Problem.loc 混合类型与 LiteratureRecord.fulltextIdent', () {
    final problem = Problem.fromJson({
      'status': 422,
      'code': 'validation_error',
      'errors': [
        {'loc': ['body', 0, 'question'], 'msg': 'bad', 'type': 'x'},
      ],
    });
    expect(problem.errors!.single.loc, ['body', '0', 'question']);
    final record = LiteratureRecord.fromJson({
      'source': 's2',
      'id': 'x',
      'pmid': ' 123 ',
      's2_id': 'abc',
      'cited_by': 5,
    });
    expect(record.fulltextIdent, '123');
    expect(record.s2Id, 'abc');
    expect(record.citedBy, 5);
  });

  test('paper_ingest 与分区表 / 机构状态的 snake_case 映射', () {
    expect(
      Job.fromJson({
        'id': 'j',
        'kind': 'paper_ingest',
        'status': 'queued',
        'created_at': '2026-09-06T14:20:57Z',
      }).kind,
      JobKind.paperIngest,
    );

    final tables = RankTables.fromJson({
      'tables': [
        {'file': 'scimagojr_2024.csv', 'year': 2024, 'journals': 30199,
          'source': 'scimago'},
      ],
      'issns': 50618,
      'titles': 30056,
      'loaded_at': '2026-09-08T10:00:00Z',
    });
    expect(tables.tables.single.journals, 30199);
    expect(tables.tables.single.year, 2024);
    expect(tables.loadedAt!.toUtc().hour, 10);

    // 空表意味着 Q1–Q4 不生效，默认值不能把它伪装成「有表」
    expect(RankTables.fromJson(const {}).tables, isEmpty);

    final paywall = PaywallStatus.fromJson({
      'configured': true,
      'saved_at': '2026-09-08T10:00:00Z',
      'final_url': 'https://www.sciencedirect.com/',
      'has_session_storage': false,
      'has_context_meta': true,
      'playwright_available': true,
    });
    expect(paywall.finalUrl, 'https://www.sciencedirect.com/');
    expect(paywall.hasSessionStorage, isFalse);
    expect(paywall.hasContextMeta, isTrue);
    expect(paywall.playwrightAvailable, isTrue);
  });
}
