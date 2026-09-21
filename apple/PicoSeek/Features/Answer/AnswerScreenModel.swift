import Foundation
import PicoSeekKit

/// 阅读器目标：第 n 篇文献，可选定位段落。
struct ReaderTarget: Identifiable, Hashable {
    var n: Int
    var pid: Int?

    var id: String { "\(n)#\(pid ?? 0)" }
}

/// 已导出的 PDF：iOS 分享面板要文件 URL，macOS 保存面板要字节，两者都带服务端给的文件名。
struct ExportedFile: Identifiable, Hashable {
    let url: URL
    let data: Data
    let name: String

    var id: String { url.path }
}

@MainActor
@Observable
final class AnswerScreenModel {
    let answerID: String

    var answer: Loadable<Answer> = .idle
    /// 旧版导入答案没有 `body_md`，回落到渲染稿（只用于避免重复拉取）。
    private var legacyMarkdown: String?
    var reader: ReaderTarget?
    /// 智能体会话的全部回合（标准答案恒为空）。
    var thread: [AnswerSummary] = []
    var monitor: JobLiveMonitor?
    /// 后台写库任务的状态；nil 表示还没有可查的任务信息。
    var backgroundKb: BackgroundKb?
    var cancelRequested = false
    var deleting = false
    var exporting = false

    private var session: SessionStore?
    private var app: AppModel?
    private var errors: ErrorPresenter?
    private var pollTask: Task<Void, Never>?
    private var kbTask: Task<Void, Never>?
    /// answer GET 的请求序号：多个刷新在途时只采用最新一次的结果。
    private var fetchSeq = 0

    init(answerID: String) {
        self.answerID = answerID
    }

    /// 离开页面时停止事件流与轮询。
    func teardown() {
        monitor?.stop()
        pollTask?.cancel()
        pollTask = nil
        kbTask?.cancel()
        kbTask = nil
    }

    var current: Answer? { answer.value }

    /// 正文模块：优先 `body_md`（带引用标记），否则用渲染稿。解析一次后缓存，
    /// 避免每次 body 求值都重新解析整篇 Markdown。
    private(set) var sections: [RenderedSection] = []
    private var renderedMarkdown: String?

    /// `structured` 为 false 时不分节。旧稿（`answer_md` 渲染稿）是整篇文档：
    /// 带问题标题、参考文献目录与免责声明，其中「参考文献」不是已知模块标签，
    /// 分节会把整份目录并进「局限」，页面出现两份参考文献。旧稿一律单块渲染。
    private func render(_ markdown: String, citationLimit: Int?, structured: Bool) {
        guard markdown != renderedMarkdown else { return }
        renderedMarkdown = markdown
        guard structured else {
            sections = [
                RenderedSection(
                    kind: .other,
                    blocks: MarkdownDocument.parse(markdown, citationLimit: citationLimit)
                )
            ]
            return
        }
        sections = AnswerSections.split(markdown).map { section in
            RenderedSection(
                kind: section.kind,
                blocks: MarkdownDocument.parse(section.markdown, citationLimit: citationLimit)
            )
        }
    }

    func configure(session: SessionStore, app: AppModel, errors: ErrorPresenter) {
        self.session = session
        self.app = app
        self.errors = errors
    }

    // MARK: - 加载

    func load() async {
        guard let client = session?.client else { return }
        await fetch(showLoading: answer.value == nil)
        startMonitorIfNeeded(client: client)
        startPollingIfNeeded()
        startKbTrackingIfNeeded()
    }

    func reload() async {
        await fetch(showLoading: false)
    }

    /// 唯一的 answer 读取入口。序号守卫保证旧的在途结果不会覆盖新结果
    /// （每个 stage 事件都会触发刷新，running 的旧响应可能晚于终态响应到达）。
    private func fetch(showLoading: Bool) async {
        guard let client = session?.client else { return }
        if showLoading { answer = .loading }
        fetchSeq += 1
        let seq = fetchSeq
        do {
            let loaded = try await client.answer(id: answerID)
            guard seq == fetchSeq else { return }
            apply(loaded)
        } catch {
            // 已有内容时保留旧状态，交给轮询继续重试；只有首屏失败才显示错误页。
            if answer.value == nil { answer = .failed(error.userMessage) }
        }
    }

    private func apply(_ loaded: Answer) {
        answer = .loaded(loaded)
        if let body = loaded.bodyMd, !body.isEmpty {
            render(body, citationLimit: loaded.papers.count, structured: true)
        }
        if loaded.status == .ready, loaded.bodyMd == nil || loaded.bodyMd?.isEmpty == true {
            Task { await loadLegacyMarkdown() }
        }
        // 会话脉络只对多轮引擎有意义：首屏拉一次，本轮进终态后再拉一次拿到新回合的标题。
        if loaded.engine != .ask, thread.isEmpty || !loaded.status.isActive {
            Task { await loadThread() }
        }
        // 只有确认答案已进入终态才停轮询：终态刷新失败时轮询是唯一兜底。
        if !loaded.status.isActive {
            pollTask?.cancel()
            pollTask = nil
        }
        startKbTrackingIfNeeded()
    }

    private func loadLegacyMarkdown() async {
        guard legacyMarkdown == nil, let client = session?.client else { return }
        legacyMarkdown = try? await client.answerMarkdown(id: answerID)
        if let legacy = legacyMarkdown { render(legacy, citationLimit: nil, structured: false) }
    }

    private func loadThread() async {
        guard let client = session?.client else { return }
        guard let turns = try? await client.answerThread(id: answerID) else { return }
        thread = turns
    }

    /// 首次进入创建监视器；从其他 Tab 回到本页时续订同一个监视器（保留已收到的阶段与日志位置）。
    private func startMonitorIfNeeded(client: APIClient) {
        guard let answer = current, answer.status.isActive, let jobID = answer.jobId else { return }
        if monitor == nil {
            monitor = JobLiveMonitor(jobID: jobID, client: client) { [weak self] live, event in
                self?.handle(live: live, event: event)
            }
        }
        guard let monitor, !monitor.isRunning else { return }
        monitor.start()
    }

    /// SSE 断开时兜底轮询；连接正常就不打扰后端。
    private func startPollingIfNeeded() {
        guard pollTask == nil, current?.status.isActive == true else { return }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                guard let self, current?.status.isActive == true else { return }
                if monitor?.connection != .open { await reload() }
            }
        }
    }

    /// 后台写库任务：API 侧 `defer_kb=True`，原子事实抽取不在问答流水线里跑，
    /// 答案交付后 worker 才建一个 `answer_kb` 子任务并挂在父任务的 `result.kb_job_id` 上。
    /// 所以只有答案已出、且本轮开启了写库时才有东西可查。
    private func startKbTrackingIfNeeded() {
        guard kbTask == nil, let answer = current, let jobID = answer.jobId else { return }
        guard answer.status == .ready, answer.engine != .codex,
              AskFilters(options: answer.options).useKb else { return }
        kbTask = Task { [weak self] in
            await self?.trackBackgroundKb(jobID: jobID)
        }
    }

    /// 父任务 → `result.kb_job_id` → 子任务两级查询；子任务未进终态时每 5 秒看一次。
    /// 请求失败按「状态暂时无法读取」处理并停止轮询（与 Web 端一致）。
    private func trackBackgroundKb(jobID: String) async {
        guard let client = session?.client else { return }
        while !Task.isCancelled {
            let parent: Job
            do {
                parent = try await client.job(id: jobID)
            } catch {
                backgroundKb = BackgroundKb(status: .unknown)
                return
            }
            guard let kbJobID = parent.result?["kb_job_id"]?.stringValue else {
                // 父任务已终态却没有子任务 id：本轮不会再写库，别再问了。
                guard parent.status.isActive, await Self.waitBeforeRefresh() else { return }
                continue
            }
            do {
                let job = try await client.job(id: kbJobID)
                backgroundKb = BackgroundKb(job: job)
                guard job.status.isActive else { return }
            } catch {
                backgroundKb = BackgroundKb(status: .unknown)
                return
            }
            guard await Self.waitBeforeRefresh() else { return }
        }
    }

    /// 轮询间隔；页面消失时任务被取消，睡眠抛错即退出。
    private static func waitBeforeRefresh() async -> Bool {
        do {
            try await Task.sleep(for: .seconds(5))
            return true
        } catch {
            return false
        }
    }

    private func handle(live: JobLive, event: SSEEvent) {
        if event.event == "stage" || live.terminal != nil {
            Task { await reload() }
        }
        if live.terminal != nil {
            app?.noteAnswersChanged()
            monitor?.stop()
        }
    }

    // MARK: - 操作

    func cancel() async {
        guard let client = session?.client, let jobID = current?.jobId else { return }
        cancelRequested = true
        do {
            try await client.cancelJob(id: jobID)
        } catch {
            cancelRequested = false
            errors?.present(error)
            return
        }
        await reload()
    }

    func delete() async -> Bool {
        guard let client = session?.client else { return false }
        deleting = true
        defer { deleting = false }
        do {
            try await client.deleteAnswer(id: answerID)
            app?.noteAnswersChanged()
            return true
        } catch {
            errors?.present(error)
            return false
        }
    }

    /// 排版全部在服务端完成，这里只把字节落到临时目录：分享面板与保存面板都要一个真实文件。
    func exportPDF() async -> ExportedFile? {
        guard let client = session?.client else { return nil }
        exporting = true
        defer { exporting = false }
        do {
            let download = try await client.answerPDF(id: answerID)
            let name = download.filename ?? "PicoSeek-\(answerID).pdf"
            let url = FileManager.default.temporaryDirectory.appending(path: name)
            try download.data.write(to: url, options: .atomic)
            return ExportedFile(url: url, data: download.data, name: name)
        } catch {
            errors?.present(error)
            return nil
        }
    }

    func openReader(_ ref: CitationRef) {
        reader = ReaderTarget(n: ref.n, pid: ref.pid)
    }

    /// 某一处引用对应的原文引文片段（用于阅读器里高亮）。
    func quotes(n: Int, pid: Int?) -> [String] {
        guard let pid, let answer = current else { return [] }
        var seen = Set<String>()
        return answer.citations
            .filter { $0.n == n && $0.pid == pid }
            .flatMap(\.quotes)
            .filter { seen.insert($0).inserted }
    }
}
