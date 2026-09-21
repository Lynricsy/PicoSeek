import 'package:flutter_test/flutter_test.dart';
import 'package:picoseek/core/api/sse.dart';
import 'package:picoseek/core/logic/ask_rail.dart';
import 'package:picoseek/core/logic/job_live.dart';

AskRailNode kbNode(BackgroundKb? kb, {bool settled = true}) =>
    askRailNodes(JobLive.empty, useKb: true, kb: kb, settled: settled).last;

void main() {
  test('写库排在综合成稿之后，答案出来前保持未开始', () {
    final nodes = askRailNodes(JobLive.empty, useKb: true);
    expect(nodes.map((n) => n.key).toList(), [
      'queries',
      'search',
      'fulltext',
      'read',
      'synthesize',
      'kb',
    ]);
    expect(nodes.last.label, '写入知识库');
    expect(nodes.last.status, RailStatus.todo);
    expect(nodes.last.hint, '答案交付后在后台进行');
    // 前序阶段没有事件时才是未开始：否则重进页面会全绿。
    expect(nodes.first.status, RailStatus.todo);
  });

  test('关掉知识库时不出现写库节点', () {
    final nodes = askRailNodes(JobLive.empty, useKb: false);
    expect(nodes.length, 5);
    expect(nodes.any((n) => n.key == 'kb'), isFalse);
  });

  test('文献解读只有解析与成稿两节点，写库仍挂在末尾', () {
    final nodes = askRailNodes(
      JobLive.empty,
      useKb: true,
      steps: StageKey.interpretPipeline,
    );
    expect(nodes.map((n) => n.key).toList(), [
      'fulltext',
      'synthesize',
      'kb',
    ]);
    expect(nodes.first.label, '获取全文');
  });

  test('流水线进行中：跑过的阶段是运行中，完成的阶段带详情摘要', () {
    final live = JobLive.empty
        .applying(
          const SseEvent(
            event: 'stage',
            data:
                '{"stage":"read","status":"finished",'
                '"detail":{"relevant":4,"total":6}}',
          ),
        )
        .applying(
          const SseEvent(
            event: 'stage',
            data: '{"stage":"synthesize","status":"started"}',
          ),
        );
    final nodes = askRailNodes(live, useKb: true);
    expect(nodes[3].status, RailStatus.done);
    expect(nodes[3].hint, '相关 4/6');
    expect(nodes[4].status, RailStatus.running);
  });

  test('答案已出：前序阶段一律完成，即使 SSE 状态是空的', () {
    final nodes = askRailNodes(JobLive.empty, useKb: true, settled: true);
    expect(nodes.take(5).every((n) => n.status == RailStatus.done), isTrue);
  });

  test('后台任务四种状态的副文案与计数', () {
    expect(
      kbNode(const BackgroundKb(status: KbStatus.queued, total: 10)),
      const AskRailNode(
        key: 'kb',
        label: '写入知识库',
        status: RailStatus.waiting,
        hint: '等待后台，优先执行新问答（0/10 篇）',
      ),
    );
    expect(
      kbNode(
        const BackgroundKb(status: KbStatus.running, current: 3, total: 10),
      ),
      const AskRailNode(
        key: 'kb',
        label: '写入知识库',
        status: RailStatus.running,
        hint: '后台写入中（3/10 篇）',
      ),
    );
    expect(
      kbNode(
        const BackgroundKb(status: KbStatus.succeeded, current: 10, total: 10),
      ),
      const AskRailNode(
        key: 'kb',
        label: '写入知识库',
        status: RailStatus.done,
        hint: '已写入 10 篇',
      ),
    );
    expect(
      kbNode(
        const BackgroundKb(status: KbStatus.failed, current: 4, total: 10),
      ),
      const AskRailNode(
        key: 'kb',
        label: '写入知识库',
        status: RailStatus.failed,
        hint: '写入失败，答案不受影响',
      ),
    );
  });

  test('取消、无计数的成功与读不到状态各有自己的说法', () {
    expect(
      kbNode(const BackgroundKb(status: KbStatus.cancelled)),
      const AskRailNode(
        key: 'kb',
        label: '写入知识库',
        status: RailStatus.cancelled,
        hint: '已取消，答案不受影响',
      ),
    );
    // total 为 0 时不拼计数，免得显示「已写入 0 篇」。
    expect(kbNode(const BackgroundKb(status: KbStatus.succeeded)).hint, '已写入');
    expect(
      kbNode(BackgroundKb.unknown),
      const AskRailNode(
        key: 'kb',
        label: '写入知识库',
        status: RailStatus.todo,
        hint: '状态暂时无法读取',
      ),
    );
  });
}
