import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/answers_version.dart';
import '../../app/theme/tokens.dart';
import '../../core/api/api_error.dart';
import '../../core/api/endpoints.dart';
import '../../core/models/answers.dart';
import '../../core/models/page.dart' as models;
import '../../core/session/session_controller.dart';
import '../../shared/format.dart';
import '../../shared/widgets/badges.dart';
import '../../shared/widgets/confirm_dialog.dart';
import '../../shared/widgets/empty_state.dart';
import '../../shared/widgets/loadable.dart';
import '../../shared/widgets/page_header.dart';
import '../../shared/widgets/pagination.dart';
import '../../shared/widgets/surface.dart';
import '../ask/engine_picker.dart';
import 'history_controller.dart';

class HistoryPage extends ConsumerWidget {
  const HistoryPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final page = ref.watch(historyControllerProvider);
    final controller = ref.read(historyControllerProvider.notifier);
    return SingleChildScrollView(
      child: PageBody(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const PageHeader(title: '问答历史'),
            TextField(
              decoration: const InputDecoration(
                prefixIcon: Icon(Icons.search_outlined),
                hintText: '搜索问题…',
              ),
              onChanged: controller.setQuery,
            ),
            const SizedBox(height: PicoSeekTokens.space3),
            Wrap(
              spacing: PicoSeekTokens.space2,
              runSpacing: PicoSeekTokens.space2,
              children: [
                ChoiceChip(
                  label: const Text('全部'),
                  selected: controller.status == null,
                  onSelected: (_) => controller.setStatus(null),
                ),
                for (final status in AnswerStatus.values)
                  ChoiceChip(
                    label: Text(answerStatusLabel(status)),
                    selected: controller.status == status,
                    onSelected: (_) => controller.setStatus(status),
                  ),
              ],
            ),
            const SizedBox(height: PicoSeekTokens.space4),
            AsyncValueView<models.Page<AnswerSummary>>(
              value: page,
              onRetry: () => unawaited(controller.reload()),
              builder: (data) => Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (data.items.isEmpty)
                    const EmptyState(
                      icon: Icons.history,
                      title: '暂无问答记录',
                      description: '没有符合当前条件的问答。',
                    )
                  else
                    for (final summary in data.items)
                      Padding(
                        padding: const EdgeInsets.only(
                          bottom: PicoSeekTokens.space3,
                        ),
                        child: _HistoryCard(
                          key: ValueKey(summary.id),
                          summary: summary,
                        ),
                      ),
                  Pager(
                    total: data.total,
                    limit: HistoryController.limit,
                    offset: controller.offset,
                    onChange: controller.setOffset,
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _HistoryCard extends ConsumerStatefulWidget {
  const _HistoryCard({super.key, required this.summary});

  final AnswerSummary summary;

  @override
  ConsumerState<_HistoryCard> createState() => _HistoryCardState();
}

class _HistoryCardState extends ConsumerState<_HistoryCard> {
  bool _busy = false;

  Future<void> _performAction() async {
    if (_busy) return;
    final summary = widget.summary;
    final cancelling = summary.status.isActive;
    setState(() => _busy = true);
    try {
      final confirmed = await showConfirm(
        context,
        title: cancelling ? '取消问答任务？' : '删除问答？',
        body: cancelling ? '任务将停止，已产生的材料仍会保留。' : '此问答将被永久删除，无法恢复。',
        destructive: !cancelling,
      );
      if (!confirmed || !mounted) return;
      final client = ref.read(apiClientProvider);
      final controller = ref.read(historyControllerProvider.notifier);
      final version = ref.read(answersVersionProvider.notifier);
      if (cancelling) {
        final jobId = summary.jobId;
        if (jobId == null) return;
        try {
          await client.cancelJob(jobId);
        } on ApiError catch (error) {
          if (error.status != 409) rethrow;
        }
        if (mounted) await controller.reload();
      } else {
        await client.deleteAnswer(summary.id);
        if (mounted) {
          final current = ref.read(historyControllerProvider).value;
          if (current != null &&
              current.items.length == 1 &&
              current.items.single.id == summary.id &&
              controller.offset > 0) {
            controller.setOffset(controller.offset - HistoryController.limit);
          }
        }
        version.bump();
      }
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(errorMessageOf(error))));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final summary = widget.summary;
    final theme = Theme.of(context);
    final active = summary.status.isActive;
    return PicoSeekCard(
      onTap: () => context.go('/a/${summary.id}'),
      padding: const EdgeInsets.all(PicoSeekTokens.space4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            summary.question,
            style: theme.textTheme.titleSmall,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
          ),
          if ((summary.rootQuestion ?? '').isNotEmpty)
            Text(
              '始于：${summary.rootQuestion}',
              style: theme.textTheme.labelSmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          const SizedBox(height: PicoSeekTokens.space2),
          Wrap(
            spacing: PicoSeekTokens.space3,
            runSpacing: PicoSeekTokens.space2,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              // 完成态不挂徽标：卡片本身就是「已完成」的常态。
              if (summary.status != AnswerStatus.ready)
                StatusBadge(status: summary.status),
              // Wrap 的 spacing 对零尺寸子项也生效，标准引擎下别留个空档。
              if (summary.engine != AnswerEngine.ask)
                EngineBadge(engine: summary.engine),
              if (summary.nTurns > 1)
                Pill(
                  text: '${summary.nTurns} 轮',
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              if (summary.filtersLabel?.isNotEmpty ?? false)
                Text(summary.filtersLabel!, style: theme.textTheme.bodySmall),
              Text(
                relativeTime(summary.createdAt),
                style: theme.textTheme.bodySmall,
              ),
              if (summary.nPapers != null)
                Text(
                  '${summary.nPapers} 篇文献',
                  style: theme.textTheme.bodySmall,
                ),
            ],
          ),
          if (summary.status == AnswerStatus.failed) ...[
            const SizedBox(height: PicoSeekTokens.space2),
            Text(
              jobErrorMessage(summary.error?.code ?? 'internal_error'),
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.error,
              ),
            ),
          ],
          if (!active || summary.jobId != null)
            Align(
              alignment: Alignment.centerRight,
              child: IconButton(
                tooltip: active ? '取消' : '删除',
                onPressed: _busy ? null : _performAction,
                icon: _busy
                    ? const SizedBox.square(
                        dimension: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : Icon(
                        active
                            ? Icons.stop_circle_outlined
                            : Icons.delete_outline,
                        color: active ? null : theme.colorScheme.error,
                      ),
              ),
            ),
        ],
      ),
    );
  }
}
