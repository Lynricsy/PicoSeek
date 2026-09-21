import Foundation

public enum JobKind: String, Codable, Sendable, Hashable {
    case ask
    case codex
    case interpret
    case kbReindex = "kb_reindex"
    case paperIngest = "paper_ingest"
    case answerKb = "answer_kb"
}

public enum JobStatus: String, Codable, Sendable, Hashable {
    case queued
    case running
    case succeeded
    case failed
    case cancelled

    public var isActive: Bool { self == .queued || self == .running }
}

public struct JobProgress: Codable, Sendable, Hashable {
    public var stage: String
    public var current: Int?
    public var total: Int?
}

public struct Job: Codable, Sendable, Hashable, Identifiable {
    public var id: String
    public var kind: JobKind
    public var status: JobStatus
    public var userId: String?
    @DefaultJSONObject public var params: JSONValue
    public var progress: JobProgress?
    public var error: JobError?
    public var result: JSONValue?
    public var createdAt: Date
    public var startedAt: Date?
    public var finishedAt: Date?
}
