import Foundation

/// 节点状态：`waiting` 是「排上队但还没轮到」，与还没走到的 `todo` 不是一回事。
public enum RailStatus: Sendable, Hashable {
    case todo
    case waiting
    case running
    case done
    case failed
    case cancelled
}

/// 流水线上的一个节点。视图只负责把它画出来，状态判定全在这里。
public struct RailNode: Sendable, Hashable, Identifiable {
    public var key: String
    public var label: String
    public var status: RailStatus
    /// 节点下的一行小字，用来承载「（1/10 篇）」这类计数与原因。
    public var hint: String?

    public var id: String { key }

    public init(key: String, label: String, status: RailStatus, hint: String? = nil) {
        self.key = key
        self.label = label
        self.status = status
        self.hint = hint
    }
}

/// 答案交付后才排队的写库任务；`unknown` 表示状态读不到，而不是没有任务。
public struct BackgroundKb: Sendable, Hashable {
    public enum Status: Sendable, Hashable {
        case queued
        case running
        case succeeded
        case failed
        case cancelled
        case unknown
    }

    public var status: Status
    public var current: Int
    public var total: Int

    public init(status: Status, current: Int = 0, total: Int = 0) {
        self.status = status
        self.current = current
        self.total = total
    }

    /// 后台子任务（`kind == "answer_kb"`）的进度：`progress = {stage:"kb", current, total}`。
    public init(job: Job) {
        self.init(
            status: Status(job.status),
            current: job.progress?.current ?? 0,
            total: job.progress?.total ?? 0
        )
    }
}

public extension BackgroundKb.Status {
    init(_ status: JobStatus) {
        switch status {
        case .queued: self = .queued
        case .running: self = .running
        case .succeeded: self = .succeeded
        case .failed: self = .failed
        case .cancelled: self = .cancelled
        }
    }
}

/// 写库不在问答流水线里跑：API 侧 `defer_kb=True`，答案交付后 worker 才建一个
/// `answer_kb` 后台任务，所以它是「综合成稿」之后的附加节点，状态来自那个独立任务。
private let kbLabel = "写入知识库"

private func kbNode(_ kb: BackgroundKb?) -> RailNode {
    guard let kb else {
        return RailNode(key: "kb", label: kbLabel, status: .todo, hint: "答案交付后在后台进行")
    }
    let count = kb.total > 0 ? "（\(kb.current)/\(kb.total) 篇）" : ""
    switch kb.status {
    case .queued:
        return RailNode(key: "kb", label: kbLabel, status: .waiting, hint: "等待后台，优先执行新问答\(count)")
    case .running:
        return RailNode(key: "kb", label: kbLabel, status: .running, hint: "后台写入中\(count)")
    case .succeeded:
        return RailNode(
            key: "kb",
            label: kbLabel,
            status: .done,
            hint: kb.total > 0 ? "已写入 \(kb.total) 篇" : "已写入"
        )
    case .failed:
        return RailNode(key: "kb", label: kbLabel, status: .failed, hint: "写入失败，答案不受影响")
    case .cancelled:
        return RailNode(key: "kb", label: kbLabel, status: .cancelled, hint: "已取消，答案不受影响")
    case .unknown:
        return RailNode(key: "kb", label: kbLabel, status: .todo, hint: "状态暂时无法读取")
    }
}

/// 答案页与运行页共用的节点序列；`settled` 表示答案已出，前序阶段一律算完成
/// （重新进入答案页时 SSE 实时状态是空的，不能让节点全灰）。
/// `steps` 让文献解读传自己的两节点流水线，其余引擎用默认的问答流水线。
/// 逐字移植 `frontend/src/components/ask/askRail.ts`。
public func askRailNodes(
    live: JobLive,
    useKb: Bool,
    kb: BackgroundKb? = nil,
    settled: Bool = false,
    steps: [StageKey] = StageKey.askPipeline
) -> [RailNode] {
    let nodes = steps.map { stage in
        RailNode(
            key: stage.rawValue,
            label: stage.label,
            status: {
                if settled || live.stages[stage]?.status == .finished { return .done }
                return live.stages[stage] == nil ? .todo : .running
            }()
        )
    }
    return useKb ? nodes + [kbNode(kb)] : nodes
}
