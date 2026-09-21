import Foundation

public enum StageKey: String, Codable, Sendable, Hashable, CaseIterable {
    case queries
    case search
    case fulltext
    case read
    case kb
    case synthesize
    case reindex
    case agent

    /// 进度面板的阶段标题。
    public var label: String {
        switch self {
        case .queries: "生成检索式"
        case .search: "检索文献"
        case .fulltext: "获取全文"
        case .read: "逐篇阅读"
        case .kb: "写入知识库"
        case .synthesize: "综合成稿"
        case .reindex: "重建索引"
        case .agent: "智能体检索与作答"
        }
    }

    /// 问答任务的阶段顺序。`reindex` 属于知识库任务；`kb`（写入知识库）也不在其中——
    /// API 侧 `defer_kb=True`，写库排到答案交付之后，由 `askRailNodes` 作为末尾节点补上。
    public static let askPipeline: [StageKey] = [.queries, .search, .fulltext, .read, .synthesize]

    /// 文献解读的阶段顺序：解析上传 PDF → 生成综述或回答。首轮写库同样由
    /// `askRailNodes` 作为末尾节点补上。
    public static let interpretPipeline: [StageKey] = [.fulltext, .synthesize]
}

/// SSE 事件归约出的实时状态。逐字移植 `frontend/src/lib/jobLive.ts`。
public struct JobLive: Sendable, Hashable {
    public struct StageState: Sendable, Hashable {
        public enum Status: Sendable, Hashable {
            case running
            case finished
        }

        public var status: Status
        public var detail: [String: JSONValue]
    }

    public struct LiveProgress: Sendable, Hashable {
        public var stage: StageKey
        public var current: Int
        public var total: Int
        public var title: String?
    }

    public struct LogLine: Sendable, Hashable, Identifiable {
        public enum Level: Sendable, Hashable {
            case info
            case warning
        }

        public var level: Level
        public var message: String
        public var id = UUID()
    }

    public struct CandidatePaper: Sendable, Hashable, Identifiable {
        public var n: Int?
        public var pmid: String?
        public var title: String?
        public var year: String?
        public var journal: String?
        public var rankLabel: String?
        public var pmcid: String?
        public var id = UUID()
    }

    public struct SearchSummary: Sendable, Hashable {
        public var candidates: Int
        public var kept: Int
        public var dropped: [String: Int]
        public var papers: [CandidatePaper]
    }

    public enum Terminal: Sendable, Hashable {
        case succeeded(answerId: String?, items: Int?, papers: Int?)
        case failed(code: String, message: String)
        case cancelled
    }

    public var stages: [StageKey: StageState] = [:]
    public var progress: LiveProgress?
    public var logs: [LogLine] = []
    public var tools: [ToolCall] = []
    public var search: SearchSummary?
    public var terminal: Terminal?

    public static let empty = JobLive()

    public init() {}

    /// 单条事件归约。未知事件、字段缺失或类型不符时原样返回，终态不短路后续事件。
    public func applying(_ event: SSEEvent) -> JobLive {
        let data = Self.object(event.data)
        var next = self

        switch event.event {
        case "stage":
            guard let raw = data["stage"]?.stringValue, let stage = StageKey(rawValue: raw),
                  let status = data["status"]?.stringValue, status == "started" || status == "finished"
            else { return self }
            let detail = data["detail"]?.objectValue ?? [:]
            next.stages[stage] = StageState(status: status == "started" ? .running : .finished, detail: detail)
            if stage == .search, status == "finished" {
                next.search = SearchSummary(
                    candidates: Self.count(detail["candidates"]),
                    kept: Self.count(detail["kept"]),
                    dropped: (detail["dropped"]?.objectValue ?? [:]).mapValues(Self.count),
                    papers: (detail["papers"]?.arrayValue ?? []).map(Self.candidate)
                )
            }
            return next

        case "progress":
            guard let raw = data["stage"]?.stringValue, let stage = StageKey(rawValue: raw) else { return self }
            next.progress = LiveProgress(
                stage: stage,
                current: Self.count(data["current"]),
                total: Self.count(data["total"]),
                title: data["title"]?.stringValue
            )
            return next

        case "tool":
            guard let call = Self.toolCall(event.data) else { return self }
            // 同一 call_id 先 started 后终态：就地替换，轨迹行不能翻倍。
            if let at = next.tools.firstIndex(where: { $0.callId == call.callId }) {
                next.tools[at] = call
            } else {
                next.tools.append(call)
            }
            return next

        case "log":
            guard let message = data["message"]?.stringValue else { return self }
            var logs = next.logs
            if logs.count >= 200 { logs.removeFirst(logs.count - 199) }
            logs.append(LogLine(level: data["level"]?.stringValue == "warning" ? .warning : .info, message: message))
            next.logs = logs
            return next

        case "succeeded":
            next.terminal = .succeeded(
                answerId: data["answer_id"]?.stringValue,
                items: data["items"]?.intValue,
                papers: data["papers"]?.intValue
            )
            return next

        case "failed":
            next.terminal = .failed(
                code: data["code"]?.stringValue ?? "internal_error",
                message: data["message"]?.stringValue ?? ""
            )
            return next

        case "cancelled":
            next.terminal = .cancelled
            return next

        default:
            return self
        }
    }

    // MARK: - 解析工具

    private static func object(_ json: String) -> [String: JSONValue] {
        guard let data = json.data(using: .utf8),
              let value = try? JSONCoding.decoder.decode(JSONValue.self, from: data) else { return [:] }
        return value.objectValue ?? [:]
    }

    private static func toolCall(_ json: String) -> ToolCall? {
        guard let data = json.data(using: .utf8) else { return nil }
        return try? JSONCoding.decoder.decode(ToolCall.self, from: data)
    }

    private static func count(_ value: JSONValue?) -> Int {
        value?.intValue ?? 0
    }

    private static func candidate(_ value: JSONValue) -> CandidatePaper {
        CandidatePaper(
            n: value["n"]?.intValue,
            pmid: value["pmid"]?.stringValue,
            title: value["title"]?.stringValue,
            year: value["year"]?.stringValue,
            journal: value["journal"]?.stringValue,
            rankLabel: value["rank_label"]?.stringValue,
            pmcid: value["pmcid"]?.stringValue
        )
    }
}

/// 阶段详情摘要文案，与 Web 端 `ProgressPipeline.summary()` 一致。
/// 说明：通用分支按键名排序输出（JSON 对象在 Swift 里无序，Web 依赖插入序）。
public func stageSummary(_ stage: StageKey, detail: [String: JSONValue]) -> String {
    if stage == .search {
        let dropped = detail["dropped"]?.objectValue ?? [:]
        let value = { (key: String) in dropped[key]?.intValue ?? 0 }
        return "候选 \(detail["candidates"]?.intValue ?? 0) → 保留 \(detail["kept"]?.intValue ?? 0)"
            + "（年份 -\(value("year")) / 分区 -\(value("quartile"))"
            + " / 未收录 -\(value("unranked")) / 期刊 -\(value("journal"))）"
    }
    if stage == .read {
        return "相关 \(detail["relevant"]?.intValue ?? 0)/\(detail["total"]?.intValue ?? 0)"
    }
    let labels = [
        "queries": "检索式", "papers": "文献", "items": "条目", "total": "总计",
        "fulltext": "全文", "n_fulltext": "全文", "chars": "字符",
        "count": "数量", "facts": "事实",
    ]
    return detail.keys.sorted().compactMap { key -> String? in
        guard let number = detail[key]?.doubleValue else { return nil }
        let text = number == number.rounded() ? String(Int(number)) : String(number)
        return "\(labels[key] ?? key) \(text)"
    }.joined(separator: " · ")
}
