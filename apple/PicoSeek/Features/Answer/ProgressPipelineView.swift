import SwiftUI
import PicoSeekKit

/// 实时进度面板：阶段列表 + 当前阶段进度 + 候选文献。
/// 刻意不展示 SSE 连接态与运行日志——那是排障信息，只有网络确实中断时才提示一句。
struct ProgressPipelineView: View {
    let live: JobLive
    let connection: JobLiveMonitor.Connection
    var useKb = true
    var engine: AnswerEngine = .ask
    /// 阶段序列：文献解读只有「解析 PDF → 生成回答」两节点，其余引擎用问答流水线。
    var steps: [StageKey] = StageKey.askPipeline
    var cancelRequested = false
    let onCancel: () -> Void

    /// 运行中的节点序列。写库节点恒为未开始：API 侧 `defer_kb=True`，
    /// 那一步排到答案交付之后才有后台任务可查。
    private var nodes: [RailNode] {
        askRailNodes(live: live, useKb: useKb, steps: steps)
    }

    private var title: String {
        switch engine {
        case .interpret: "文献解读"
        case .codex: "智能体检索"
        case .ask: "正在生成答案"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Label(title, systemImage: "sparkles")
                    .font(.headline)
                    .symbolEffect(.pulse)
                Spacer()
                Button(cancelRequested ? "取消中…" : "取消", action: onCancel)
                    .buttonStyle(.plain)
                    .font(.subheadline)
                    .foregroundStyle(.red)
                    .disabled(cancelRequested)
            }

            if engine == .codex {
                // 轨迹本身就是进展，不用文字复述系统在干什么；
                // 还没有轨迹时只给一个转圈表示在跑。
                Group {
                    if live.tools.isEmpty {
                        ProgressView().controlSize(.small)
                    } else {
                        TraceListView(calls: live.tools)
                    }
                }
                .animation(.default, value: live.tools)
            } else {
                StageRailView(nodes: nodes) { node in
                    stageDetail(node)
                }
            }

            if connection == .reconnecting {
                Label("网络不稳定，正在重新连接…", systemImage: "wifi.exclamationmark")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            candidates
        }
        .card(padding: 16)
    }

    @ViewBuilder
    private var candidates: some View {
        if let papers = live.search?.papers, !papers.isEmpty {
            DisclosureGroup("候选文献（\(papers.count)）") {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(papers) { paper in
                        HStack(alignment: .top, spacing: 8) {
                            Text(paper.n.map { "\($0)" } ?? "·")
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.secondary)
                                .frame(width: 22, alignment: .trailing)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(paper.title ?? "（无标题）")
                                    .font(.caption)
                                    .lineLimit(2)
                                Text([paper.journal, paper.year].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            if let label = paper.rankLabel, !label.isEmpty {
                                Pill(text: label)
                            }
                        }
                    }
                }
                .padding(.top, 6)
            }
            .font(.subheadline)
        }
    }

    /// 节点下的阶段细节：结束后的摘要、进行中的进度条。节点 key 就是阶段 rawValue。
    @ViewBuilder
    private func stageDetail(_ node: RailNode) -> some View {
        if let stage = StageKey(rawValue: node.key), let state = live.stages[stage] {
            // 只在阶段结束后给摘要：进行中的 detail 只有 total，会渲染出一串 0。
            if state.status == .finished {
                let caption = Self.stageCaption(stage, detail: state.detail)
                if !caption.isEmpty {
                    Text(caption)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            if state.status == .running, live.terminal == nil, let progress = live.progress,
               progress.stage == stage, progress.total > 0 {
                ProgressView(value: Double(progress.current), total: Double(progress.total))
                    .tint(Color.accentColor)
                if let title = progress.title, !title.isEmpty {
                    Text(title)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
        }
    }

    /// 面向用户的阶段摘要。kit 的 `stageSummary` 与 Web 端逐字对齐（含 `dropped` 明细等
    /// 排障字段），这里只挑用户关心的数字重写一份。
    private static func stageCaption(_ stage: StageKey, detail: [String: JSONValue]) -> String {
        func int(_ key: String) -> Int { detail[key]?.intValue ?? 0 }
        switch stage {
        case .queries:
            guard let count = detail["queries"]?.arrayValue?.count else { return "" }
            return "\(count) 条检索式"
        case .search:
            return "候选 \(int("candidates")) 篇，保留 \(int("kept")) 篇"
        case .fulltext:
            return "\(int("fulltext"))/\(int("total")) 篇获得全文"
        case .read:
            return "\(int("relevant"))/\(int("total")) 篇相关"
        case .kb:
            return "知识库共 \(int("items")) 条知识"
        case .synthesize, .reindex, .agent:
            return ""
        }
    }
}
