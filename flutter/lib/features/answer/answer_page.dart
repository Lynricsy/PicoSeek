import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/answers_version.dart';
import '../../app/theme/app_theme.dart';
import '../../app/theme/tokens.dart';
import '../../core/api/api_error.dart';
import '../../core/api/endpoints.dart';
import '../../core/logic/ask_filters.dart';
import '../../core/logic/ask_rail.dart';
import '../../core/logic/citations.dart';
import '../../core/logic/job_live.dart';
import '../../core/models/answers.dart';
import '../../core/session/session_controller.dart';
import '../../shared/export_file.dart';
import '../../shared/format.dart';
import '../../shared/widgets/badges.dart';
import '../../shared/widgets/confirm_dialog.dart';
import '../../shared/widgets/floating_composer.dart';
import '../../shared/widgets/loadable.dart';
import '../../shared/widgets/page_header.dart';
import '../../shared/widgets/surface.dart';
import '../ask/ask_state.dart';
import '../ask/composer.dart';
import '../ask/engine_picker.dart';
import '../ask/filter_sheet.dart';
import '../reader/reader_pane.dart';
import '../reader/split_view.dart';
import 'answer_body.dart';
import 'answer_controller.dart';
import 'background_kb.dart';
import 'job_live_monitor.dart';
import 'progress_pipeline.dart';
import 'source_list.dart';
import 'stage_rail.dart';
import 'thread_nav.dart';
import 'trace_list.dart';

class AnswerPage extends ConsumerStatefulWidget {
  const AnswerPage({super.key, required this.answerId});

  final String answerId;

  @override
  ConsumerState<AnswerPage> createState() => _AnswerPageState();
}

class _AnswerPageState extends ConsumerState<AnswerPage> {
  final _composer = TextEditingController();
  bool _submitting = false;
  bool _busy = false;

  @override
  void dispose() {
    _composer.dispose();
    super.dispose();
  }

  void _openReader(CitationRef ref_) {
    final width = MediaQuery.sizeOf(context).width;
    ref.read(readerTargetProvider(widget.answerId).notifier).open(ref_);
    if (width >= PicoSeekTokens.expandedMinWidth) return; // 并排布局直接更新
    if (width < PicoSeekTokens.compactMaxWidth) {
      showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        useSafeArea: true,
        builder: (context) => FractionallySizedBox(
          heightFactor: 0.92,
          child: _ReaderHost(answerId: widget.answerId),
        ),
      ).whenComplete(_closeReader);
      return;
    }
    showGeneralDialog<void>(
      context: context,
      barrierDismissible: true,
      barrierLabel: '关闭阅读器',
      transitionDuration: MediaQuery.disableAnimationsOf(context)
          ? Duration.zero
          : PicoSeekTokens.motionFast,
      pageBuilder: (context, animation, secondary) => Align(
        alignment: Alignment.centerRight,
        child: Material(
          color: Theme.of(context).colorScheme.surface,
          child: SizedBox(
            width: _sideSheetWidth(context),
            height: double.infinity,
            child: SafeArea(child: _ReaderHost(answerId: widget.answerId)),
          ),
        ),
      ),
      transitionBuilder: (context, animation, secondary, child) =>
          SlideTransition(
            position: Tween<Offset>(begin: const Offset(1, 0), end: Offset.zero)
                .animate(
                  CurvedAnimation(
                    parent: animation,
                    curve: PicoSeekTokens.motionCurve,
                  ),
                ),
            child: child,
          ),
    ).whenComplete(_closeReader);
  }

  void _closeReader() {
    if (!mounted) return;
    ref.read(readerTargetProvider(widget.answerId).notifier).close();
  }

  /// 新建一轮问答（标准引擎，或智能体引擎尚未 ready 时）。
  Future<void> _submitNew(String question) async {
    await _submit(() async {
      final filters = ref.read(askFiltersControllerProvider);
      return ref
          .read(apiClientProvider)
          .createAnswer(filters.toAnswerCreate(question));
    });
  }

  /// 追问：续接同一智能体会话，其余选项沿用被追问的那一轮。
  Future<void> _submitFollowUp(String question) async {
    await _submit(
      () => ref
          .read(apiClientProvider)
          .followUp(widget.answerId, FollowupCreate(question: question)),
    );
  }

  Future<void> _submit(Future<Answer> Function() request) async {
    setState(() => _submitting = true);
    try {
      final answer = await request();
      ref.read(answersVersionProvider.notifier).bump();
      ref.invalidate(answerThreadProvider(widget.answerId));
      _composer.clear();
      if (!mounted) return;
      context.go('/a/${answer.id}');
    } on ApiError catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.userMessage)));
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  Future<void> _cancel() async {
    final confirmed = await showConfirm(
      context,
      title: '取消任务',
      body: '已产生的检索与阅读结果会保留，但不会生成答案。',
    );
    if (!confirmed) return;
    setState(() => _busy = true);
    try {
      await ref
          .read(answerControllerProvider(widget.answerId).notifier)
          .cancel();
    } on ApiError catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.userMessage)));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _delete() async {
    final confirmed = await showConfirm(
      context,
      title: '删除问答',
      body: '删除后不可恢复。',
      destructive: true,
    );
    if (!confirmed) return;
    setState(() => _busy = true);
    try {
      await ref
          .read(answerControllerProvider(widget.answerId).notifier)
          .delete();
      if (!mounted) return;
      context.go('/history');
    } on ApiError catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.userMessage)));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// 导出 PDF：排版全在服务端，客户端只把字节交给系统保存或分享。
  Future<void> _exportPdf() async {
    setState(() => _busy = true);
    try {
      final download = await ref.read(apiClientProvider).answerPdf(widget.answerId);
      final path = await saveOrSharePdf(
        bytes: download.bytes,
        filename: download.filename ?? 'PicoSeek-${widget.answerId}.pdf',
      );
      if (!mounted) return;
      // Android 走分享面板，去向由用户在面板里决定，再提示路径只会误导。
      if (path != null && !Platform.isAndroid) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('已保存到 $path')));
      }
    } on ApiError catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.userMessage)));
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('导出失败：$error')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _reuseFilters(Answer answer) {
    ref
        .read(askFiltersControllerProvider.notifier)
        .set(AskFilters.fromOptions(answer.options));
    ref.read(askDraftProvider.notifier).set(answer.question);
    context.go('/');
  }

  @override
  Widget build(BuildContext context) {
    final width = MediaQuery.sizeOf(context).width;
    final wide = width >= PicoSeekTokens.expandedMinWidth;
    final target = ref.watch(readerTargetProvider(widget.answerId));
    final answerAsync = ref.watch(answerControllerProvider(widget.answerId));

    final main = AsyncValueView<Answer>(
      value: answerAsync,
      onRetry: () => ref.invalidate(answerControllerProvider(widget.answerId)),
      builder: (answer) => _AnswerContent(
        answer: answer,
        busy: _busy,
        submitting: _submitting,
        composer: _composer,
        onOpenReader: _openReader,
        onCancel: _cancel,
        onDelete: _delete,
        onExportPdf: _exportPdf,
        onReuse: () => _reuseFilters(answer),
        // 智能体与文献解读的已完成回合才能续接，其余情况一律新建一轮。
        onSubmit:
            (answer.engine.supportsFollowUp &&
                answer.status == AnswerStatus.ready)
            ? _submitFollowUp
            : _submitNew,
      ),
    );

    if (wide && target != null) {
      return SplitView(
        left: main,
        // 阅读器滚动不该把提问框收起来。
        right: NotificationListener<ScrollNotification>(
          onNotification: (_) => true,
          child: _ReaderHost(answerId: widget.answerId, onClose: _closeReader),
        ),
      );
    }
    return main;
  }
}

double _sideSheetWidth(BuildContext context) {
  final width = MediaQuery.sizeOf(context).width * 0.85;
  return width < 560 ? width : 560;
}

/// 阅读器宿主：跟随 `readerTargetProvider`，目标为空时显示提示。
class _ReaderHost extends ConsumerWidget {
  const _ReaderHost({required this.answerId, this.onClose});

  final String answerId;
  final VoidCallback? onClose;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final target = ref.watch(readerTargetProvider(answerId));
    if (target == null) return const SizedBox.shrink();
    final theme = Theme.of(context);
    return Container(
      decoration: BoxDecoration(
        color: theme.colorScheme.surface,
        border: Border(
          left: BorderSide(color: theme.colorScheme.outlineVariant),
        ),
      ),
      child: ReaderPane(
        answerId: answerId,
        target: target,
        onClose: onClose ?? () => Navigator.of(context).maybePop(),
      ),
    );
  }
}

class _AnswerContent extends ConsumerWidget {
  const _AnswerContent({
    required this.answer,
    required this.busy,
    required this.submitting,
    required this.composer,
    required this.onOpenReader,
    required this.onCancel,
    required this.onDelete,
    required this.onExportPdf,
    required this.onReuse,
    required this.onSubmit,
  });

  final Answer answer;
  final bool busy;
  final bool submitting;
  final TextEditingController composer;
  final void Function(CitationRef ref) onOpenReader;
  final Future<void> Function() onCancel;
  final Future<void> Function() onDelete;
  final Future<void> Function() onExportPdf;
  final VoidCallback onReuse;
  final void Function(String question) onSubmit;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final jobId = answer.jobId;
    final liveState = (answer.status.isActive && jobId != null)
        ? ref.watch(jobLiveMonitorProvider(jobId))
        : null;
    final cancelRequested = ref
        .watch(answerControllerProvider(answer.id).notifier)
        .cancelRequested;
    final codex = answer.engine == AnswerEngine.codex;
    final interpret = answer.engine == AnswerEngine.interpret;
    // 只有多轮会话才有线程，标准引擎不必为此多发一个请求。
    final thread = answer.engine.supportsFollowUp
        ? (ref.watch(answerThreadProvider(answer.id)).value ??
              const <AnswerSummary>[])
        : const <AnswerSummary>[];

    final followUp =
        answer.engine.supportsFollowUp && answer.status == AnswerStatus.ready;
    // 写库对智能体不生效；其余引擎都是答案交付后才排后台任务。
    final useKb = !codex && answer.options['use_kb'] != false;

    return FloatingComposerHost(
      body: SingleChildScrollView(
        child: PageBody(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _AnswerHeader(
                answer: answer,
                thread: thread,
                onReuse: onReuse,
                onDelete: (busy || answer.status.isActive) ? null : onDelete,
                onExportPdf:
                    (busy || answer.status != AnswerStatus.ready)
                    ? null
                    : onExportPdf,
              ),
              const SizedBox(height: PicoSeekTokens.sectionSpacing),
              if (answer.status.isActive)
                liveState == null
                    ? const LoadingView()
                    : PicoSeekCard(
                        child: ProgressPipeline(
                          state: liveState,
                          engine: answer.engine,
                          steps: interpret
                              ? StageKey.interpretPipeline
                              : StageKey.askPipeline,
                          title: interpret ? '文献解读' : null,
                          useKb: useKb,
                          onCandidateTap: (pmid) =>
                              ScaffoldMessenger.of(context).showSnackBar(
                                const SnackBar(
                                  content: Text(
                                    '任务仍在进行中，原文与核实材料会在逐篇阅读阶段结束后出现。',
                                  ),
                                ),
                              ),
                          onCancel:
                              (busy || cancelRequested || answer.jobId == null)
                              ? null
                              : onCancel,
                          cancelRequested: cancelRequested,
                        ),
                      ),
              // 失败只给中文错误标题：后端 message 是排障信息，不给用户看。
              if (answer.status == AnswerStatus.failed)
                PicoSeekCard(
                  tint: theme.colorScheme.error,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(
                            Icons.error_outline,
                            size: 18,
                            color: theme.colorScheme.error,
                          ),
                          const SizedBox(width: PicoSeekTokens.space3),
                          Expanded(
                            child: Text(
                              jobErrorMessage(
                                answer.error?.code ?? 'internal_error',
                              ),
                              style: theme.textTheme.labelLarge?.copyWith(
                                color: theme.colorScheme.error,
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: PicoSeekTokens.space3),
                      Align(
                        alignment: Alignment.centerLeft,
                        child: OutlinedButton(
                          onPressed: onReuse,
                          child: Text(
                            answer.engine == AnswerEngine.ask
                                ? '放宽筛选后重新提问'
                                : '重新提问',
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              if (answer.status == AnswerStatus.cancelled)
                _NoticeCard(
                  icon: Icons.stop_circle_outlined,
                  color: context.picoseek.warning,
                  title: '任务已取消',
                  body: '可以调整筛选后重新提问。',
                  action: OutlinedButton(
                    onPressed: onReuse,
                    child: const Text('重新提问'),
                  ),
                ),
              if (answer.status == AnswerStatus.ready) ...[
                if (useKb && jobId != null)
                  _BackgroundKbRail(
                    jobId: jobId,
                    steps: interpret
                        ? StageKey.interpretPipeline
                        : StageKey.askPipeline,
                  ),
                _ReadyBody(answer: answer, onOpenReader: onOpenReader),
              ],
            ],
          ),
        ),
      ),
      composer: Composer(
        controller: composer,
        onSubmit: onSubmit,
        submitting: submitting,
        // 智能体与文献解读的已完成回合可以续接，输入框锁定引擎并换占位文案。
        mode: followUp ? ComposerMode.followUp : ComposerMode.ask,
        sessionEngine: answer.engine,
        hintText: followUp ? null : '继续提问…',
        onFilterTap: followUp ? null : () => showFilterSheet(context),
      ),
    );
  }
}

class _ReadyBody extends ConsumerWidget {
  const _ReadyBody({required this.answer, required this.onOpenReader});

  final Answer answer;
  final void Function(CitationRef ref) onOpenReader;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final bodyMd = answer.bodyMd;
    // 智能体没有逐篇原文快照：正文不做结构化拆分，也没有来源列表可给。
    if (answer.engine == AnswerEngine.codex) {
      return _AgentBody(
        answer: answer,
        bodyMd: bodyMd ?? '',
        onOpenReader: onOpenReader,
      );
    }
    // 旧版答案没有结构化正文，取渲染稿 Markdown。
    if (bodyMd == null || bodyMd.trim().isEmpty) {
      final legacy = ref.watch(legacyAnswerMarkdownProvider(answer.id));
      return AsyncValueView<String>(
        value: legacy,
        onRetry: () => ref.invalidate(legacyAnswerMarkdownProvider(answer.id)),
        builder: (markdown) => _BodyWithSources(
          answer: answer,
          bodyMd: markdown,
          structured: false,
          onOpenReader: onOpenReader,
        ),
      );
    }
    return _BodyWithSources(
      answer: answer,
      bodyMd: bodyMd,
      structured: true,
      onOpenReader: onOpenReader,
    );
  }
}

/// 答案页的阶段节点条：答案已出，前序阶段一律算完成，末尾挂后台写库的实时状态。
/// 重进页面时 SSE 状态是空的，所以走 `settled: true` 而不是等事件重放。
class _BackgroundKbRail extends ConsumerWidget {
  const _BackgroundKbRail({required this.jobId, required this.steps});

  final String jobId;

  /// 前序阶段序列：文献解读只有解析与成稿两节点。
  final List<StageKey> steps;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final kb = ref.watch(backgroundKbWatcherProvider(jobId)).value;
    // 还查不到后台任务时不占版面：答案本身已经交付，节点条只是补充信息。
    if (kb == null) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: PicoSeekTokens.moduleSpacing),
      child: PicoSeekCard(
        child: StageRail(
          nodes: askRailNodes(
            JobLive.empty,
            useKb: true,
            kb: kb,
            settled: true,
            steps: steps,
          ),
          progress: kb.status == KbStatus.running && kb.total > 0
              ? (
                  label: StageKey.kb.label,
                  current: kb.current,
                  total: kb.total,
                  detail: null,
                )
              : null,
        ),
      ),
    );
  }
}

/// 智能体答案：整篇正文 + 可折叠的检索轨迹。
class _AgentBody extends StatelessWidget {
  const _AgentBody({
    required this.answer,
    required this.bodyMd,
    required this.onOpenReader,
  });

  final Answer answer;
  final String bodyMd;
  final void Function(CitationRef ref) onOpenReader;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        AnswerBody(
          bodyMd: bodyMd,
          papers: answer.papers,
          onCitationTap: onOpenReader,
          structured: false,
        ),
        if (answer.trace.isNotEmpty) ...[
          const SizedBox(height: PicoSeekTokens.moduleSpacing),
          PicoSeekCard(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
            child: ExpansionTile(
              childrenPadding: const EdgeInsets.only(bottom: 10),
              title: Text(
                '检索轨迹（${answer.trace.length}）',
                style: theme.textTheme.labelLarge,
              ),
              children: [TraceList(calls: answer.trace)],
            ),
          ),
        ],
        if (answer.kbHits.isNotEmpty) ...[
          const SizedBox(height: PicoSeekTokens.moduleSpacing),
          KbSupplementList(hits: answer.kbHits),
        ],
      ],
    );
  }
}

class _BodyWithSources extends StatelessWidget {
  const _BodyWithSources({
    required this.answer,
    required this.bodyMd,
    required this.structured,
    required this.onOpenReader,
  });

  final Answer answer;
  final String bodyMd;
  final bool structured;
  final void Function(CitationRef ref) onOpenReader;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      AnswerBody(
        bodyMd: bodyMd,
        papers: answer.papers,
        onCitationTap: onOpenReader,
        structured: structured,
      ),
      const SizedBox(height: PicoSeekTokens.moduleSpacing),
      SourceList(papers: answer.papers, bodyMd: bodyMd, onOpen: onOpenReader),
      if (answer.kbHits.isNotEmpty) ...[
        const SizedBox(height: PicoSeekTokens.moduleSpacing),
        KbSupplementList(hits: answer.kbHits),
      ],
    ],
  );
}

/// 答案页头部：元信息一行 + 更多菜单 + 会话导航 + 问题 + 筛选摘要。
class _AnswerHeader extends StatelessWidget {
  const _AnswerHeader({
    required this.answer,
    required this.thread,
    required this.onReuse,
    required this.onDelete,
    required this.onExportPdf,
  });

  final Answer answer;
  final List<AnswerSummary> thread;
  final VoidCallback onReuse;

  /// 为 null 时菜单里的删除项禁用（活动中或正忙）。
  final Future<void> Function()? onDelete;

  /// 为 null 时菜单里的导出项禁用（答案未就绪或正忙）。
  final Future<void> Function()? onExportPdf;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final wide = MediaQuery.sizeOf(context).width >= PicoSeekTokens.compactMaxWidth;
    final caption = theme.textTheme.labelSmall?.copyWith(
      color: theme.colorScheme.onSurfaceVariant,
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Wrap(
                spacing: 6,
                runSpacing: 4,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Text(relativeTime(answer.createdAt), style: caption),
                  if (answer.status == AnswerStatus.ready &&
                      answer.nPapers != null) ...[
                    Text('·', style: caption),
                    Text('${answer.nPapers} 篇文献', style: caption),
                  ],
                  if (answer.engine != AnswerEngine.ask)
                    EngineBadge(engine: answer.engine),
                ],
              ),
            ),
            // 导出是主动作：塞进「更多」菜单没人找得到，就绪时独立成按钮
            if (answer.status == AnswerStatus.ready)
              Padding(
                padding: const EdgeInsets.only(right: PicoSeekTokens.space2),
                child: OutlinedButton.icon(
                  onPressed: onExportPdf,
                  icon: const Icon(Icons.picture_as_pdf_outlined, size: 18),
                  label: const Text('导出 PDF'),
                ),
              ),
            _AnswerMenu(
              answer: answer,
              onReuse: onReuse,
              onDelete: onDelete,
            ),
          ],
        ),
        if (thread.length > 1) ...[
          const SizedBox(height: PicoSeekTokens.space3),
          ThreadNav(turns: thread, currentId: answer.id),
        ],
        const SizedBox(height: PicoSeekTokens.space3),
        SelectableText(
          answer.question,
          style: wide
              ? theme.textTheme.headlineMedium
              : theme.textTheme.headlineSmall,
        ),
        if ((answer.filtersLabel ?? '').isNotEmpty) ...[
          const SizedBox(height: PicoSeekTokens.space2),
          Pill(
            text: answer.filtersLabel!,
            color: theme.colorScheme.onSurfaceVariant,
            icon: Icons.filter_list,
          ),
        ],
      ],
    );
  }
}

enum _AnswerAction { reuse, queries, delete }

/// 头部右侧「更多」菜单：重新提问 / 查看检索式 / 删除。导出 PDF 已独立成按钮。
class _AnswerMenu extends StatelessWidget {
  const _AnswerMenu({
    required this.answer,
    required this.onReuse,
    required this.onDelete,
  });

  final Answer answer;
  final VoidCallback onReuse;
  final Future<void> Function()? onDelete;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final standard = answer.engine == AnswerEngine.ask;
    return PopupMenuButton<_AnswerAction>(
      tooltip: '更多',
      icon: const Icon(Icons.more_horiz),
      onSelected: (action) {
        switch (action) {
          case _AnswerAction.reuse:
            onReuse();
          case _AnswerAction.queries:
            showQueriesSheet(context, answer.queries);
          case _AnswerAction.delete:
            onDelete?.call();
        }
      },
      itemBuilder: (context) => [
        PopupMenuItem(
          value: _AnswerAction.reuse,
          // 只有标准引擎有「筛选」这层语义，其余引擎只是重新问一次。
          child: Text(standard ? '沿用筛选重新提问' : '重新提问'),
        ),
        PopupMenuItem(
          value: _AnswerAction.queries,
          enabled: answer.queries.isNotEmpty,
          child: const Text('查看检索式'),
        ),
        const PopupMenuDivider(),
        PopupMenuItem(
          value: _AnswerAction.delete,
          enabled: onDelete != null,
          child: Text('删除', style: TextStyle(color: scheme.error)),
        ),
      ],
    );
  }
}

/// 检索式单独成面：不占正文位置。
void showQueriesSheet(BuildContext context, List<String> queries) {
  final theme = Theme.of(context);
  final lines = [
    for (final query in queries)
      Padding(
        padding: const EdgeInsets.only(bottom: PicoSeekTokens.space2),
        child: SelectableText(
          query,
          style: theme.textTheme.labelSmall?.merge(monoStyle),
        ),
      ),
  ];

  if (MediaQuery.sizeOf(context).width < PicoSeekTokens.compactMaxWidth) {
    showModalBottomSheet<void>(
      context: context,
      useSafeArea: true,
      isScrollControlled: true,
      builder: (context) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.5,
        maxChildSize: 0.9,
        builder: (context, controller) => ListView(
          controller: controller,
          padding: const EdgeInsets.all(PicoSeekTokens.pageInset),
          children: [
            Text('检索式', style: theme.textTheme.titleMedium),
            const SizedBox(height: PicoSeekTokens.space3),
            ...lines,
          ],
        ),
      ),
    );
    return;
  }

  showDialog<void>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('检索式'),
      content: SizedBox(
        width: 560,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: lines,
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('完成'),
        ),
      ],
    ),
  );
}

class _NoticeCard extends StatelessWidget {
  const _NoticeCard({
    required this.icon,
    required this.color,
    required this.title,
    this.body,
    this.action,
  });

  final IconData icon;
  final Color color;
  final String title;
  final String? body;

  /// 文字下方的行动按钮。
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return PicoSeekCard(
      tint: color,
      padding: const EdgeInsets.all(PicoSeekTokens.space4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 18, color: color),
          const SizedBox(width: PicoSeekTokens.space3),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: theme.textTheme.labelLarge?.copyWith(color: color),
                ),
                if (body != null) ...[
                  const SizedBox(height: PicoSeekTokens.space1),
                  Text(body!, style: theme.textTheme.bodySmall),
                ],
                if (action != null) ...[
                  const SizedBox(height: PicoSeekTokens.space3),
                  Align(alignment: Alignment.centerLeft, child: action!),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}
