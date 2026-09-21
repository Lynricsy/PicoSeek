import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme/app_theme.dart';
import '../../app/theme/tokens.dart';
import '../../core/models/answers.dart';
import '../../shared/widgets/surface.dart';
import 'ask_state.dart';
import 'engine_picker.dart';

/// 提问（新建一轮）还是追问（续接智能体会话）。
enum ComposerMode { ask, followUp }

/// 悬浮提问框：毛玻璃面板 + 单行起步的输入区 + 引擎/筛选芯片 + 圆形发送键。
///
/// 物理键盘 Enter 提交、Shift+Enter 换行；移动端换行键正常换行。
class Composer extends ConsumerStatefulWidget {
  const Composer({
    super.key,
    required this.controller,
    required this.onSubmit,
    this.submitting = false,
    this.autofocus = false,
    this.hintText,
    this.mode = ComposerMode.ask,
    this.sessionEngine,
    this.onFilterTap,
  });

  final TextEditingController controller;
  final void Function(String question) onSubmit;
  final bool submitting;
  final bool autofocus;

  /// 未给时按 [mode] 取默认占位。
  final String? hintText;
  final ComposerMode mode;

  /// 追问态下本次会话的引擎；未给时按智能体渲染。
  final AnswerEngine? sessionEngine;

  /// 非空且 [mode] 为 [ComposerMode.ask] 时显示筛选摘要芯片。
  final VoidCallback? onFilterTap;

  @override
  ConsumerState<Composer> createState() => _ComposerState();
}

class _ComposerState extends ConsumerState<Composer> {
  static const maxLength = 2000;

  /// 超过此字数才提示剩余额度（平时不占视觉噪音）。
  static const _countThreshold = 1800;

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onChanged);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onChanged);
    super.dispose();
  }

  void _onChanged() => setState(() {});

  bool get _isAsk => widget.mode == ComposerMode.ask;

  String get _hint =>
      widget.hintText ??
      switch (widget.mode) {
        ComposerMode.ask => '请输入临床或科研问题…',
        ComposerMode.followUp => '追问这个话题…',
      };

  void _submit(bool canSubmit) {
    if (!canSubmit) return;
    widget.onSubmit(widget.controller.text.trim());
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final filters = ref.watch(askFiltersControllerProvider);
    final yearValid = filters.isYearRangeValid();
    final length = widget.controller.text.characters.length;
    final canSubmit =
        !widget.submitting &&
        widget.controller.text.trim().isNotEmpty &&
        (!_isAsk || yearValid);
    final showFilterChip = widget.onFilterTap != null && _isAsk;

    return GlassPanel(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          CallbackShortcuts(
            bindings: {
              const SingleActivator(LogicalKeyboardKey.enter): () =>
                  _submit(canSubmit),
              const SingleActivator(LogicalKeyboardKey.numpadEnter): () =>
                  _submit(canSubmit),
            },
            child: TextField(
              controller: widget.controller,
              autofocus: widget.autofocus,
              minLines: 1,
              maxLines: 6,
              maxLength: maxLength,
              maxLengthEnforcement: MaxLengthEnforcement.enforced,
              keyboardType: TextInputType.multiline,
              textInputAction: TextInputAction.newline,
              style: theme.textTheme.bodyLarge,
              decoration: InputDecoration(
                hintText: _hint,
                counterText: '',
                border: InputBorder.none,
                enabledBorder: InputBorder.none,
                focusedBorder: InputBorder.none,
                filled: false,
                isDense: true,
                contentPadding: EdgeInsets.zero,
              ),
            ),
          ),
          if (_isAsk && !yearValid)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(
                '筛选里的年份范围无效，请先修正。',
                style: theme.textTheme.labelSmall?.copyWith(
                  color: scheme.error,
                ),
              ),
            ),
          const SizedBox(height: 10),
          Row(
            children: [
              EnginePicker(
                value: filters.engine,
                onChanged: (engine) => ref
                    .read(askFiltersControllerProvider.notifier)
                    .set(filters.copyWith(engine: engine)),
                lockedEngine: widget.mode == ComposerMode.followUp
                    ? (widget.sessionEngine ?? AnswerEngine.codex)
                    : null,
              ),
              if (showFilterChip) ...[
                const SizedBox(width: PicoSeekTokens.space2),
                Flexible(
                  child: FilterSummaryChip(
                    summary: filters.summary,
                    onTap: widget.onFilterTap!,
                  ),
                ),
              ],
              const Spacer(),
              if (length >= _countThreshold)
                Padding(
                  padding: const EdgeInsets.only(right: PicoSeekTokens.space2),
                  child: Text(
                    '$length/$maxLength',
                    style: theme.textTheme.labelSmall
                        ?.merge(monoStyle)
                        .copyWith(color: scheme.onSurfaceVariant),
                  ),
                ),
              IconButton.filled(
                onPressed: canSubmit ? () => _submit(canSubmit) : null,
                tooltip: 'Enter 发送 · Shift+Enter 换行',
                style: IconButton.styleFrom(
                  fixedSize: const Size(32, 32),
                  minimumSize: const Size(32, 32),
                  padding: EdgeInsets.zero,
                  shape: const CircleBorder(),
                  disabledBackgroundColor: scheme.onSurface.withValues(
                    alpha: 0.12,
                  ),
                ),
                icon: widget.submitting
                    ? const SizedBox.square(
                        dimension: 14,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.arrow_upward, size: 16),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// 提问框里的筛选入口：显示当前筛选摘要，点按打开筛选面板。
class FilterSummaryChip extends StatelessWidget {
  const FilterSummaryChip({
    super.key,
    required this.summary,
    required this.onTap,
  });

  final String summary;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => ActionChip(
    avatar: const Icon(Icons.filter_list),
    label: Text(summary, maxLines: 1, overflow: TextOverflow.ellipsis),
    tooltip: '检索筛选',
    onPressed: onTap,
  );
}
