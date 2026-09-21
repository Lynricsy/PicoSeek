import 'package:flutter/material.dart';

import '../../core/models/answers.dart';
import '../../shared/widgets/badges.dart';

/// 引擎的展示元信息（名称 / 副标题 / 图标），三端字符串完全一致。
extension AnswerEngineDisplay on AnswerEngine {
  String get label => switch (this) {
    AnswerEngine.ask => '标准',
    AnswerEngine.codex => '智能体',
    AnswerEngine.interpret => '解读',
  };

  String get hint => switch (this) {
    AnswerEngine.ask => '固定流水线：检索 → 全文 → 逐篇阅读 → 综合，结论可逐条溯源',
    AnswerEngine.codex => 'Codex 自主决定检索路径，可多轮追问；不提供逐篇原文快照',
    AnswerEngine.interpret => '上传 PDF 后基于原文问答',
  };

  IconData get icon => switch (this) {
    AnswerEngine.ask => Icons.list_alt_outlined,
    AnswerEngine.codex => Icons.auto_awesome,
    AnswerEngine.interpret => Icons.article_outlined,
  };
}

/// 提问框内的引擎选择器。追问态（[lockedEngine] 非空）沿用本次会话的引擎，
/// 只显示不可交互的锁定态。
class EnginePicker extends StatelessWidget {
  const EnginePicker({
    super.key,
    required this.value,
    required this.onChanged,
    this.lockedEngine,
  });

  final AnswerEngine value;
  final ValueChanged<AnswerEngine> onChanged;

  /// 本次会话锁定的引擎；null 表示未锁定（新建一轮）。
  final AnswerEngine? lockedEngine;

  @override
  Widget build(BuildContext context) {
    final locked = lockedEngine;
    if (locked != null) {
      return Tooltip(
        message: '追问会沿用本次会话的引擎',
        child: Chip(
          avatar: Icon(locked.icon, size: 14),
          label: Text(
            locked == AnswerEngine.interpret ? '文献解读 · 继续提问' : '智能体 · 续接对话',
          ),
        ),
      );
    }

    final theme = Theme.of(context);
    return MenuAnchor(
      alignmentOffset: const Offset(0, 6),
      menuChildren: [
        // 文献解读只能由上传 PDF 创建，不在提问框里出现。
        for (final engine in const [AnswerEngine.ask, AnswerEngine.codex])
          MenuItemButton(
            leadingIcon: engine == value
                ? const Icon(Icons.check, size: 16)
                : Icon(engine.icon, size: 16),
            onPressed: () => onChanged(engine),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 300),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(engine.label, style: theme.textTheme.bodyMedium),
                  Text(
                    engine.hint,
                    style: theme.textTheme.labelSmall?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
          ),
      ],
      builder: (context, controller, child) => ActionChip(
        avatar: Icon(value.icon, size: 14),
        label: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(value.label),
            const Icon(Icons.expand_more, size: 14),
          ],
        ),
        tooltip: value.hint,
        onPressed: () =>
            controller.isOpen ? controller.close() : controller.open(),
      ),
    );
  }
}

/// 引擎徽标：标准引擎不显示（减少噪音），其余引擎各自一枚。
class EngineBadge extends StatelessWidget {
  const EngineBadge({super.key, required this.engine});

  final AnswerEngine engine;

  @override
  Widget build(BuildContext context) {
    if (engine == AnswerEngine.ask) return const SizedBox.shrink();
    return Pill(
      text: engine.label,
      color: Theme.of(context).colorScheme.primary,
      icon: engine.icon,
    );
  }
}
