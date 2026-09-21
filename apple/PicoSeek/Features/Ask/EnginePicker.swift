import SwiftUI
import PicoSeekKit

/// 引擎名称与一句话说明。三端同一份文案，用户在哪端读到的都是同一句。
/// 文献解读只能由上传 PDF 创建，不进这份可选列表。
struct EngineOption: Identifiable {
    let engine: AnswerEngine
    let title: String
    let hint: String
    let symbol: String

    var id: AnswerEngine { engine }

    static let all: [EngineOption] = [
        EngineOption(
            engine: .ask,
            title: "标准",
            hint: "固定流水线：检索 → 全文 → 逐篇阅读 → 综合，结论可逐条溯源",
            symbol: "list.bullet.rectangle"
        ),
        EngineOption(
            engine: .codex,
            title: "智能体",
            hint: "Codex 自主决定检索路径，可多轮追问；不提供逐篇原文快照",
            symbol: "sparkles"
        ),
    ]

    static func of(_ engine: AnswerEngine) -> EngineOption {
        all.first { $0.engine == engine } ?? all[0]
    }
}

/// 引擎选择器：胶囊菜单，放在提问框内与筛选芯片并列。
struct EnginePicker: View {
    @Binding var engine: AnswerEngine
    /// 追问态：引擎由会话决定，中途换不了，只显示当前是谁在答；`nil` 表示未锁定。
    var lockedEngine: AnswerEngine?

    var body: some View {
        if let lockedEngine {
            chip(symbol: Self.lockedSymbol(lockedEngine), title: Self.lockedTitle(lockedEngine))
                .help("追问会沿用本次会话的引擎")
        } else {
            Menu {
                ForEach(EngineOption.all) { option in
                    Button {
                        engine = option.engine
                    } label: {
                        // 菜单项两行：名称 + 它到底怎么干活；选中项把图标换成对勾。
                        Label(
                            option.title,
                            systemImage: engine == option.engine ? "checkmark" : option.symbol
                        )
                        Text(option.hint)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            } label: {
                let current = EngineOption.of(engine)
                chip(symbol: current.symbol, title: current.title, chevron: true)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("选择引擎")
        }
    }

    /// 与 `QuestionComposer.filterChip` 同一套视觉，两个芯片并排不能各长各样。
    private func chip(symbol: String, title: String, chevron: Bool = false) -> some View {
        HStack(spacing: 4) {
            Image(systemName: symbol)
            Text(title)
                .lineLimit(1)
            if chevron {
                Image(systemName: "chevron.down")
                    .font(.caption2)
            }
        }
        .font(.caption.weight(.medium))
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(.fill.tertiary, in: .capsule)
        .foregroundStyle(.secondary)
    }

    /// 锁定态文案：解读续的是上传的那组 PDF，与智能体会话不是一回事。
    private static func lockedTitle(_ engine: AnswerEngine) -> String {
        engine == .interpret ? "文献解读 · 继续提问" : "智能体 · 续接对话"
    }

    private static func lockedSymbol(_ engine: AnswerEngine) -> String {
        engine == .interpret ? "doc.text.magnifyingglass" : "sparkles"
    }
}

/// 标准引擎不挂徽标：它是默认值，标出来只是噪音。
struct EngineBadge: View {
    let engine: AnswerEngine

    var body: some View {
        switch engine {
        case .ask:
            EmptyView()
        case .codex:
            Pill(text: "智能体", tone: .accentColor, systemImage: "sparkles")
        case .interpret:
            Pill(text: "解读", tone: .accentColor, systemImage: "doc.text.magnifyingglass")
        }
    }
}
