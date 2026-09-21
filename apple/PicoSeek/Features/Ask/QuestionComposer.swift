import SwiftUI
import PicoSeekKit

/// 提问框的两种用途：新建提问，或在智能体会话上追问。
enum ComposerMode {
    case ask
    case followUp
}

extension ComposerMode {
    /// 追问续接同一会话，占位得说清这一点，别看着像在开新话题。
    func hint(placeholder: String) -> String {
        self == .followUp ? "追问这个话题…" : placeholder
    }
}

/// 提问框的三档形态。仿 Safari 地址栏：静止时收成单行，向下滚动再横向缩成居中小药丸，
/// 一旦聚焦、有草稿或正在提交就长回完整形态（并随键盘抬到合适位置）。
enum ComposerForm: Equatable {
    case expanded
    case compact
    case minimized
}

extension ComposerForm {
    /// 收起态趋近胶囊。
    var cornerRadius: CGFloat {
        self == .expanded ? Metrics.composerRadius : 23
    }

    /// 玻璃容器内边距。收起态左右多留一点，单行文字才不贴边。
    var insets: EdgeInsets {
        self == .expanded
            ? EdgeInsets(top: 14, leading: 16, bottom: 10, trailing: 16)
            : EdgeInsets(top: 13, leading: 18, bottom: 13, trailing: 18)
    }

    /// 最小形态横向收窄成居中小药丸，正文左右两侧都能露出来。
    var maxWidth: CGFloat {
        self == .minimized ? 220 : .infinity
    }
}

/// 提问输入框：多行输入 + 字数 + 筛选摘要 + 发送。提问页与答案页底部追问共用。
struct QuestionComposer: View {
    @Binding var text: String
    var placeholder = "输入临床或科研问题…"
    var pending = false
    var focused: FocusState<Bool>.Binding?
    var mode: ComposerMode = .ask
    /// 追问时会话所属的引擎（智能体或文献解读），决定引擎芯片的锁定态文案。
    var sessionEngine: AnswerEngine?
    /// 是否允许收起。提问首页的输入框是页面主体，必须常驻完整形态；
    /// 答案页的主体是答案正文，输入框要给它让位。
    var collapsible = false
    /// 宿主滚动视图是否正在向下滚动（且已离开顶部）。
    var scrolledDown = false
    let onSubmit: () -> Void

    @Environment(AppModel.self) private var app
    @FocusState private var localFocus: Bool
    @State private var showFilters = false
    @State private var sendCount = 0

    private var trimmed: String { text.trimmingCharacters(in: .whitespacesAndNewlines) }

    /// 外部传入焦点绑定时以它为准，否则用自己的——同一时刻只有一个绑定接到输入框上。
    private var isFocused: Bool { focused?.wrappedValue ?? localFocus }

    /// 聚焦、有草稿、正在提交这三种情况用户都要看到内容与控件，必须完整展开。
    private var form: ComposerForm {
        guard collapsible else { return .expanded }
        if isFocused || pending || !trimmed.isEmpty { return .expanded }
        return scrolledDown ? .minimized : .compact
    }

    /// 追问不带筛选：年份草稿有问题也不该拦住它。
    private var yearRangeUsable: Bool {
        mode == .followUp || app.filters.isYearRangeValid()
    }

    private var canSubmit: Bool {
        !pending && !trimmed.isEmpty && trimmed.count <= 2000 && yearRangeUsable
    }

    var body: some View {
        @Bindable var app = app
        VStack(alignment: .leading, spacing: 10) {
            textField

            // 收起态只留输入框本身：此时必然没有草稿，发送与筛选都无从谈起。
            if form == .expanded {
                if !yearRangeUsable {
                    Text("筛选里的年份范围无效，请先修正。")
                        .font(.caption)
                        .foregroundStyle(.red)
                }

                HStack(spacing: 8) {
                    EnginePicker(
                        engine: $app.filters.engine,
                        lockedEngine: mode == .followUp ? (sessionEngine ?? .codex) : nil
                    )
                    if mode == .ask {
                        filterChip
                    }
                    Spacer(minLength: 8)
                    // 只在接近上限时提示字数，常驻计数器是噪音。
                    if trimmed.count >= 1800 {
                        Text("\(trimmed.count)/2000")
                            .font(.caption2)
                            .monospacedDigit()
                            .contentTransition(.numericText())
                            .foregroundStyle(trimmed.count > 2000 ? .red : .secondary)
                    }
                    sendButton
                }
            }
        }
        .padding(form.insets)
        .glassEffect(.regular, in: .rect(cornerRadius: form.cornerRadius))
        // 先夹住宽度再套一层全宽容器：最小形态因此居中，其余形态照旧铺满。
        .frame(maxWidth: form.maxWidth)
        .frame(maxWidth: .infinity)
        .animation(.snappy(duration: 0.28), value: form)
        .sensoryFeedback(.impact(weight: .light), trigger: sendCount)
        .sheet(isPresented: $showFilters) {
            FilterSheet()
        }
    }

    private var filterChip: some View {
        Button {
            showFilters = true
        } label: {
            Label(app.filters.summary, systemImage: "line.3.horizontal.decrease")
                .font(.caption.weight(.medium))
                .lineLimit(1)
                .truncationMode(.tail)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(.fill.tertiary, in: .capsule)
        }
        .buttonStyle(.plain)
        .foregroundStyle(.secondary)
    }

    private var sendButton: some View {
        Button {
            sendCount += 1
            onSubmit()
        } label: {
            ZStack {
                Circle().fill(canSubmit ? Color.accentColor : Color.secondary.opacity(0.25))
                if pending {
                    ProgressView().controlSize(.small).tint(.white)
                } else {
                    Image(systemName: "arrow.up")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(canSubmit ? .white : .secondary)
                }
            }
            .frame(width: 32, height: 32)
        }
        .buttonStyle(.plain)
        .disabled(!canSubmit)
        .keyboardShortcut(.return, modifiers: .command)
        .accessibilityLabel("发送")
        .animation(.snappy, value: canSubmit)
    }

    /// 输入框在三档形态里始终留在视图树上：焦点与草稿因此不会在收放之间丢失，
    /// 点收起的药丸就是直接点进输入框，不需要额外的手势去抢焦点。
    @ViewBuilder
    private var textField: some View {
        let field = TextField(mode.hint(placeholder: placeholder), text: $text, axis: .vertical)
            .lineLimit(form == .expanded ? 1 ... 6 : 1 ... 1)
            .textFieldStyle(.plain)
            .font(.body)
        if let focused {
            field.focused(focused)
        } else {
            field.focused($localFocus)
        }
    }
}

/// 提交提问的共用流程：创建任务 → 通知列表刷新 → 返回新答案。
@MainActor
enum AskSubmission {
    static func create(
        question: String,
        session: SessionStore,
        app: AppModel,
        errors: ErrorPresenter
    ) async -> Answer? {
        guard let client = session.client else { return nil }
        let trimmed = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        do {
            let answer = try await client.createAnswer(app.filters.answerCreate(question: trimmed))
            app.noteAnswersChanged()
            return answer
        } catch {
            if error.code == "too_many_jobs" {
                errors.present(message: error.userMessage)
            } else {
                errors.present(error)
            }
            return nil
        }
    }
}
