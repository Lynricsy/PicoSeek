import Foundation
import Testing
@testable import PicoSeekKit

/// 手写模型与后端契约的对齐：字段名、未知枚举回落、自由形状参数的键名。
@Suite("模型解码")
struct ModelDecodingTests {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONCoding.decoder.decode(type, from: Data(json.utf8))
    }

    /// 后端将来再加引擎时，这份答案要还能显示，而不是整页解码失败。
    @Test("未知 engine 回落标准引擎")
    func unknownEngineFallsBack() throws {
        let summary = try decode(AnswerSummary.self, """
        {"id":"a1","status":"ready","question":"问题","engine":"quantum",
        "created_at":"2026-09-08T10:00:00Z","n_turns":1}
        """)
        #expect(summary.engine == .ask)
    }

    @Test("codex 任务的 kind 可解码")
    func codexJobKind() throws {
        let job = try decode(Job.self, """
        {"id":"j1","kind":"codex","status":"running","created_at":"2026-09-08T10:00:00Z"}
        """)
        #expect(job.kind == .codex)
    }

    /// 文献解读是第三个引擎：答案与后台任务都要认得，否则答案页整页解码失败。
    @Test("interpret 的 engine 与 kind 可解码")
    func interpretEngineAndJobKind() throws {
        let summary = try decode(AnswerSummary.self, """
        {"id":"a3","status":"ready","question":"梳理这组文献的组织关系与差异","engine":"interpret",
        "created_at":"2026-09-08T10:00:00Z","n_turns":2}
        """)
        #expect(summary.engine == .interpret)

        let job = try decode(Job.self, """
        {"id":"j2","kind":"interpret","status":"running","created_at":"2026-09-08T10:00:00Z"}
        """)
        #expect(job.kind == .interpret)
    }

    @Test("Answer 解出会话字段与检索轨迹")
    func decodesTrace() throws {
        let answer = try decode(Answer.self, """
        {"id":"a2","status":"ready","question":"再总结一句","engine":"codex",
        "created_at":"2026-09-08T10:00:00Z","n_turns":2,"root_question":"根问题","parent_id":"a1",
        "queries":[],"papers":[],"citations":[],"kb_hits":[],
        "trace":[{"call_id":"c1","server":"semantic_scholar","tool":"read_pdf","status":"completed",
        "args":{"paper_id":"P1","section":"results"},"duration_ms":820,"error":null}]}
        """)
        #expect(answer.engine == .codex)
        #expect(answer.parentId == "a1")
        #expect(answer.nTurns == 2)
        #expect(answer.rootQuestion == "根问题")

        let call = try #require(answer.trace.first)
        #expect(call.callId == "c1")
        #expect(call.status == .completed)
        #expect(call.durationMs == 820)
        #expect(call.error == nil)
        // args 是自由形状：键名必须保持后端原样，否则参数摘要取不到 paper_id。
        #expect(call.args["paper_id"]?.stringValue == "P1")
        #expect(ToolPresentation.describeArgs(call.args) == "「P1」 · results")
    }

    @Test("未知工具状态按终态处理，缺省 args 为空对象")
    func unknownToolStatus() throws {
        let call = try decode(ToolCall.self, """
        {"call_id":"c1","server":"shell","tool":"exec","status":"inProgress"}
        """)
        #expect(call.status == .completed)
        #expect(call.args.objectValue?.isEmpty == true)
        #expect(call.id == "c1")
    }
}
