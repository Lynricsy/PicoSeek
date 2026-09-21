import Foundation

public enum AnswerStatus: String, Codable, Sendable, Hashable, CaseIterable {
    case queued
    case running
    case ready
    case failed
    case cancelled

    /// 任务尚未进入终态（需要 SSE / 轮询继续跟踪）。
    public var isActive: Bool { self == .queued || self == .running }
}

/// 问答引擎：`ask` 走固定证据流水线，`codex` 交给智能体自主决定检索路径，
/// `interpret` 解读上传的 PDF（只能由上传入口创建，提问框里选不到）。
public enum AnswerEngine: String, Codable, Sendable, Hashable, CaseIterable {
    case ask
    case codex
    case interpret

    /// 后端未来新增引擎时不至于整份答案解码失败。
    public init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = AnswerEngine(rawValue: raw) ?? .ask
    }
}

public struct JobError: Codable, Sendable, Hashable {
    public var code: String
    public var message: String

    public init(code: String, message: String) {
        self.code = code
        self.message = message
    }
}

/// 一篇被阅读的文献在答案中的元信息。注意 `year` 是字符串，未知字段一律为 `""`。
public struct AnswerPaper: Codable, Sendable, Hashable, Identifiable {
    public var n: Int
    public var pmid: String
    public var doi: String
    public var pmcid: String
    public var title: String
    public var year: String
    public var journal: String
    public var issn: String
    public var authors: String
    public var quartile: String
    public var rankLabel: String
    public var source: PaperSource
    public var relevance: Int?
    public var nParagraphs: Int
    public var nCitations: Int
    public var nCitationsVerified: Int

    public var id: Int { n }
}

public enum PaperSource: String, Codable, Sendable, Hashable {
    case pmc
    case pdf
    case inst
    case upload
    case abstract

    /// 后端未来新增来源时不至于整份答案解码失败。
    public init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = PaperSource(rawValue: raw) ?? .abstract
    }

    public var label: String {
        switch self {
        case .pmc: "全文 · PMC"
        case .pdf: "全文 · PDF"
        case .inst: "全文 · 机构"
        case .upload: "全文 · 上传"
        case .abstract: "仅摘要"
        }
    }
}

/// 答案正文引用到的一个原文段落。
public struct Citation: Codable, Sendable, Hashable {
    public var n: Int
    public var pmid: String
    public var pid: Int
    public var sec: String
    public var page: Int?
    public var text: String
    public var quotes: [String]
    public var fromMarker: Bool
}

public enum ToolCallStatus: String, Codable, Sendable, Hashable {
    case started
    case completed
    case failed

    /// 未知状态按终态处理：轨迹里挂一个永远转圈的行比标成已完成更误导。
    public init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = ToolCallStatus(rawValue: raw) ?? .completed
    }
}

/// 智能体的一次工具调用。SSE `tool` 事件与 `Answer.trace` 的元素是同一份形状，
/// 实时轨迹与落库轨迹因此共用一套解析与渲染。
public struct ToolCall: Codable, Sendable, Hashable, Identifiable {
    public var callId: String
    public var server: String
    public var tool: String
    public var status: ToolCallStatus
    /// 后端只保留标量参数，键名保持后端原样（`paper_id`、`pmids` 等）。
    @DefaultJSONObject public var args: JSONValue
    public var durationMs: Int?
    public var error: String?

    /// 同一 `call_id` 的 started 与终态是同一行，用它做 upsert 与列表标识。
    public var id: String { callId }

    public init(
        callId: String,
        server: String,
        tool: String,
        status: ToolCallStatus,
        args: JSONValue = .object([:]),
        durationMs: Int? = nil,
        error: String? = nil
    ) {
        self.callId = callId
        self.server = server
        self.tool = tool
        self.status = status
        self.args = args
        self.durationMs = durationMs
        self.error = error
    }
}

public struct AnswerSummary: Codable, Sendable, Hashable, Identifiable {
    public var id: String
    public var jobId: String?
    public var status: AnswerStatus
    public var question: String
    public var engine: AnswerEngine
    public var filtersLabel: String?
    public var nPapers: Int?
    public var nFulltext: Int?
    public var createdAt: Date
    public var finishedAt: Date?
    public var error: JobError?
    /// 同一会话的回合数（智能体追问会累加），非会话答案恒为 1。
    public var nTurns: Int
    /// 本行是追问时给出会话的根问题，用于历史列表的「始于：…」。
    public var rootQuestion: String?
}

public struct Answer: Codable, Sendable, Hashable, Identifiable {
    public var id: String
    public var jobId: String?
    public var status: AnswerStatus
    public var question: String
    public var engine: AnswerEngine
    public var filtersLabel: String?
    public var nPapers: Int?
    public var nFulltext: Int?
    public var createdAt: Date
    public var finishedAt: Date?
    public var error: JobError?
    public var nTurns: Int
    public var rootQuestion: String?
    public var questionEn: String?
    /// 上一轮答案（追问时非空）。
    public var parentId: String?
    public var queries: [String]
    @DefaultJSONObject public var options: JSONValue
    public var startedAt: Date?
    public var papers: [AnswerPaper]
    public var bodyMd: String?
    public var citations: [Citation]
    public var kbHits: [KbHit]
    /// 智能体本轮的检索轨迹（`ask` 引擎恒为空）。
    public var trace: [ToolCall]
}

/// 逐篇阅读材料：在 `AnswerPaper` 之上追加笔记、核实引文、事实与全文。
public struct AnswerPaperDetail: Codable, Sendable, Hashable {
    public var n: Int
    public var pmid: String
    public var doi: String
    public var pmcid: String
    public var title: String
    public var year: String
    public var journal: String
    public var issn: String
    public var authors: String
    public var quartile: String
    public var rankLabel: String
    public var source: PaperSource
    public var relevance: Int?
    public var nParagraphs: Int
    public var nCitations: Int
    public var nCitationsVerified: Int
    public var notesMd: String
    public var citations: [VerifiedQuote]
    public var facts: [Fact]
    public var paragraphs: [Paragraph]
    public var fulltextMd: String
}

/// 创建问答任务的请求体。可选字段为 nil 时不编码（后端对 years / year_from 互斥有校验）。
public struct AnswerCreate: Codable, Sendable, Hashable {
    public var question: String
    public var engine: AnswerEngine
    public var papers: Int
    public var years: Int?
    public var yearFrom: Int?
    public var yearTo: Int?
    public var quartiles: [Int]
    public var journals: [String]
    public var keepUnranked: Bool?
    public var useKb: Bool
    /// 智能体引擎不走确定性流水线，这两项对它无效时一律不发，
    /// 免得后端记下一个从未生效的预算。
    public var kbHits: Int?
    public var maxChars: Int?

    public init(
        question: String,
        engine: AnswerEngine = .ask,
        papers: Int,
        years: Int? = nil,
        yearFrom: Int? = nil,
        yearTo: Int? = nil,
        quartiles: [Int] = [],
        journals: [String] = [],
        keepUnranked: Bool? = nil,
        useKb: Bool = true,
        kbHits: Int? = 0,
        maxChars: Int? = 28000
    ) {
        self.question = question
        self.engine = engine
        self.papers = papers
        self.years = years
        self.yearFrom = yearFrom
        self.yearTo = yearTo
        self.quartiles = quartiles
        self.journals = journals
        self.keepUnranked = keepUnranked
        self.useKb = useKb
        self.kbHits = kbHits
        self.maxChars = maxChars
    }
}

/// 在已有智能体会话上追问；其余选项一律沿用被追问的那一轮。
public struct FollowupCreate: Codable, Sendable, Hashable {
    public var question: String

    public init(question: String) {
        self.question = question
    }
}
