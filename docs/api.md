# PicoSeek API v1 协议

本文面向浏览器前端、其他客户端与公开 API 用户，描述 PicoSeek 当前实现的 HTTP wire contract。端点与模型的机器可读定义以仓库中的 [`backend/openapi.json`](../backend/openapi.json) 为准。

## 1. 通用约定

### 1.1 基础路径与接口描述

- API base path：`/v1`
- OpenAPI JSON：`GET /v1/openapi.json`
- Swagger UI：`GET /v1/docs`
- ReDoc：`GET /v1/redoc`
- 下文示例使用服务地址 `http://localhost:8765`。

健康检查、上述接口描述入口和 `POST /v1/auth/login` 免鉴权；其余 `/v1` 资源端点需要有效用户会话。

### 1.2 鉴权

先由管理员创建账号，再使用用户名密码登录。之后统一在请求头中传入会话令牌：

```http
Authorization: Bearer <access_token>
```

仅支持 `user` 和 `admin` 两种角色，不开放注册。用户名 3-64 字符，匹配 `^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$`，统一小写；密码为 6-128 字符，不修剪空白。密码以 Argon2id 哈希保存，随机会话令牌只存 SHA-256 摘要。登录响应含 `access_token`、`token_type: "bearer"`、`expires_at` 和 `user`；会话默认固定有效期 7 天，不自动续期，不提供刷新令牌。

令牌缺失、无效、过期、被撤销，或账号已禁用时返回 `401 unauthenticated`，并带 `WWW-Authenticate: Bearer`。注销仅撤销当前会话；修改/重置密码、禁用账号撤销全部会话；重新启用不会恢复旧令牌。生产环境必须使用 HTTPS。

SSE 也只接受 Bearer 请求头；所有端点均不再接受 `?access_token=`。原生浏览器 `EventSource` 不适用，使用支持请求头的 fetch 流客户端。不要把令牌写入 URL、日志或 Markdown 引用。

| 角色 | 能力 |
|---|---|
| `user` | 读取和管理自己的 answers、jobs、阅读材料及 SSE；共享读取 papers、KB、期刊与上游文献。 |
| `admin` | 具备普通用户能力，可查看和管理全部问答、任务与历史无归属资源，重建 KB 并管理账号。 |

问答、阅读快照与任务是私有资源，非所有者一律得到 `404`，不暴露是否存在。文献与衍生知识继续共享；知识提取使用问题作为上下文，因此不是严格的用户隐私或租户隔离，不应提交敏感个人或患者信息。

### 1.3 JSON、时间与分页

- JSON 字段统一使用 `snake_case`。
- 所有资源 ID 均为字符串；文献段落号、答案中的论文序号等明确声明为整数的字段除外。
- API 生成的时间戳为 ISO-8601 UTC、秒精度并带 `Z`，例如 `2026-09-05T12:34:56Z`。
- 分页列表统一接受 `limit`（整数，`1..100`，默认 `20`）与 `offset`（整数，`>=0`，默认 `0`），返回：

```json
{"items": [], "total": 0, "limit": 20, "offset": 0}
```

- answers 与 jobs 按 `created_at` 降序；papers 按 `indexed_at` 降序。相同时间下服务端可使用资源 ID 保持稳定次序。

### 1.4 CORS 与限流

CORS 由 `PICOSEEK_CORS_ORIGINS` 配置，默认空列表，即不添加跨域放行中间件。启用后允许所有 HTTP 方法，请求头允许 `Authorization`、`Content-Type` 与 `Last-Event-ID`，响应暴露 `Location`、`Retry-After`、`WWW-Authenticate`、`Content-Disposition`（PDF 导出的文件名靠它带出）。不用 Cookie，不需要浏览器 `credentials: "include"`。

登录按归一化后的用户名使用 Redis 固定窗口限流，含成功登录：默认 300 秒最多 10 次，超出返回 `429 login_rate_limited` 及剩余秒数 `Retry-After`；窗口不因重试延长。配置为 `PICOSEEK_LOGIN_MAX_ATTEMPTS`、`PICOSEEK_LOGIN_WINDOW_S`。Redis 不可用时登录返回 `503 unavailable`，不绕过限流；已有会话仍由数据库验证。

业务接口没有通用请求速率限制。每个用户的 `queued` 与 `running` job 合计达到 `PICOSEEK_MAX_ACTIVE_JOBS_PER_USER`（默认 `2`）后，`POST /v1/answers`、`POST /v1/answers/interpret`、`POST /v1/papers/upload` 与 `POST /v1/papers/ingest` 返回 `429 too_many_jobs`；同一用户的多个会话共享额度。多篇带问题的文献解读一次入队两轮，整批判定，占两个名额。

## 2. 错误模型

错误采用 RFC 9457 Problem Details，响应媒体类型为 `application/problem+json`，包括请求校验产生的 `422`。客户端应依据稳定的 `code` 分支，不应依赖可能调整的 `detail` 文案。就绪探针的依赖故障 `503` 是明确例外，使用 `ReadinessResponse`，详见健康检查。

| 字段 | 类型 | 说明 |
|---|---|---|
| `type` | `string` | 错误类型 URI，形式为 `urn:picoseek:error:{code}`。 |
| `title` | `string` | HTTP 状态的标准英文短语。 |
| `status` | `integer` | HTTP 状态码。 |
| `detail` | `string` | 面向人的具体错误说明。 |
| `instance` | `string` | 触发错误的请求路径，不含查询字符串。 |
| `code` | `string` | 供客户端分支处理的稳定错误码。 |
| `errors` | `array<object> \| null` | 仅请求结构校验失败时出现；每项包含 `loc: string[]`、`msg: string`、`type: string`。 |

真实响应形状示例：

```json
{
  "type": "urn:picoseek:error:not_found",
  "title": "Not Found",
  "status": 404,
  "detail": "answer 'abc' not found",
  "instance": "/v1/answers/abc",
  "code": "not_found"
}
```

错误码如下。这里的 HTTP 错误 `code` 与异步任务的 `job.error.code` 是两套枚举。

| code | HTTP 状态 | 含义 | 典型触发场景 |
|---|---:|---|---|
| `unauthenticated` | 401 | 未通过鉴权 | 错误用户名/密码；缺失、无效、过期或撤销的会话；账号禁用。 |
| `forbidden` | 403 | 角色不足 | 普通用户管理账号或重建 KB。 |
| `not_found` | 404 | 资源不存在或不可见 | 普通用户访问他人的 answer、job 或阅读材料也返回 404。 |
| `fulltext_unavailable` | 404 | 无可用 PMC 全文 | 标识符不能映射到 PMCID，或 Europe PMC 没有全文。 |
| `not_ready` | 409 | answer 尚未就绪 | 在 answer 为 `queued`、`running`、`failed` 或 `cancelled` 时请求渲染稿。 |
| `conflict` | 409 | 当前资源状态不允许操作 | 取消已终态 job；活跃 answer 缺少关联 job。 |
| `username_exists` | 409 | 用户名已被占用 | 大小写不敏感的重复建号。 |
| `cannot_disable_self` | 409 | 禁止禁用自己 | 管理员禁用当前账号。 |
| `last_admin` | 409 | 必须保留活跃管理员 | 禁用最后一个活跃管理员。 |
| `validation_error` | 422 | 参数或请求体校验失败 | 参数越界、年份规则冲突、期刊查询没有 `issn` 与 `title`。框架级校验失败另带 `errors` 数组。 |
| `too_many_jobs` | 429 | 当前用户活跃任务达到上限 | 同一用户所有会话合计的 `queued`/`running` 数达到配置值。 |
| `payload_too_large` | 413 | 上传内容超过大小上限 | `POST /v1/papers/upload` 的 PDF 超过 `PICOSEEK_UPLOAD_MAX_MB`；`PUT /v1/paywall/state` 的单份 JSON 超过 5 MB。 |
| `login_rate_limited` | 429 | 登录窗口内请求过多 | 同一用户名达到限额；读取 `Retry-After` 后再试。 |
| `upstream_unavailable` | 502 | Redis、PubMed、Semantic Scholar 或 Europe PMC 等上游不可用 | 入队失败、上游超时、上游限流或返回错误。`detail` 会指出来源。 |
| `unavailable` | 503 | 服务依赖不可用 | HTTP 层产生 503 时的默认错误码。就绪探针自身会以其健康响应形状直接返回 503。 |
| `internal_error` | 500 | 未处理的服务端错误 | 服务端内部异常；响应不会暴露堆栈。 |

422 示例：

```json
{
  "type": "urn:picoseek:error:validation_error",
  "title": "Unprocessable Content",
  "status": 422,
  "detail": "Input should be less than or equal to 30",
  "instance": "/v1/answers",
  "code": "validation_error",
  "errors": [
    {"loc": ["body", "papers"], "msg": "Input should be less than or equal to 30", "type": "less_than_equal"}
  ]
}
```

## 3. 端点总表

表中路径与 `backend/openapi.json` 的 `paths` 一一对应。一个路径可能支持多个方法。

| 方法 | 路径 | 访问要求 | 说明 |
|---|---|---|---|
| `GET` | `/v1/health` | 无 | 存活探针。 |
| `GET` | `/v1/health/ready` | 无 | DB、Redis、LLM、KB、期刊分区就绪检查。 |
| `POST` | `/v1/auth/login` | 无 | 用户名密码登录，受登录限流保护。 |
| `POST` | `/v1/auth/logout` | 登录 | 注销本次会话。 |
| `GET` | `/v1/auth/me` | 登录 | 读取当前用户。 |
| `POST` | `/v1/auth/password` | 登录 | 校验当前密码，改密并撤销全部会话。 |
| `GET` / `POST` | `/v1/users` | `admin` | 分页浏览或创建用户。 |
| `GET` / `PATCH` | `/v1/users/{user_id}` | `admin` | 读取账号或切换启用状态。 |
| `POST` | `/v1/users/{user_id}/password` | `admin` | 重置密码并撤销全部会话。 |
| `POST` | `/v1/answers` | 登录 | 创建异步问答任务。 |
| `POST` | `/v1/answers/interpret` | 登录 | 上传 1..N 篇 PDF 创建文献解读会话（multipart）。 |
| `GET` | `/v1/answers` | 登录 | 分页浏览 answer。 |
| `GET` | `/v1/answers/{answer_id}` | 登录 | 获取 answer 详情。 |
| `DELETE` | `/v1/answers/{answer_id}` | 本人或 `admin` | 仅删除终态结果；活跃时返回 `409`。 |
| `GET` | `/v1/answers/{answer_id}/markdown` | 登录 | 获取带链接的完整 Markdown 渲染稿。 |
| `GET` | `/v1/answers/{answer_id}/pdf` | 登录 | 导出排版后的 PDF；服务端统一渲染，三端一致。 |
| `GET` | `/v1/answers/{answer_id}/papers/{n}` | 登录 | 获取本次问答阅读的第 `n` 篇详情。 |
| `GET` | `/v1/answers/{answer_id}/papers/{n}/markdown` | 登录 | 获取本次阅读原文快照，保留段落锚点。 |
| `POST` | `/v1/answers/{answer_id}/followup` | 本人 | 在智能体或文献解读会话上追问。 |
| `GET` | `/v1/answers/{answer_id}/thread` | 登录 | 获取同一会话的全部回合。 |
| `GET` | `/v1/jobs` | 登录 | 本人任务；`admin` 查看全部。 |
| `GET` | `/v1/jobs/{job_id}` | 登录 | 获取可见 job 详情。 |
| `POST` | `/v1/jobs/{job_id}/cancel` | 本人或 `admin` | 请求取消活跃 job，接受后返回 `202`。 |
| `GET` | `/v1/jobs/{job_id}/events` | 登录 | 订阅 job 的 SSE 事件流。 |
| `GET` | `/v1/papers` | 登录 | 分页浏览本地文献库。 |
| `GET` | `/v1/papers/{key}` | 登录 | 获取文献元数据。 |
| `GET` | `/v1/papers/{key}/paragraphs` | 登录 | 获取文献全部段落。 |
| `GET` | `/v1/papers/{key}/paragraphs/{pid}` | 登录 | 获取单个段落。 |
| `GET` | `/v1/papers/{key}/facts` | 登录 | 获取原子事实。 |
| `GET` | `/v1/papers/{key}/fulltext` | 登录 | 获取带段落锚点的 Markdown 全文。 |
| `POST` | `/v1/papers/upload` | 登录 | 上传 PDF 异步入库，返回 `202` 与 `paper_ingest` job。 |
| `POST` | `/v1/papers/ingest` | 登录 | 按 DOI 经机构订阅取全文入库，返回 `202`。 |
| `GET` | `/v1/kb/search` | 登录 | 语义检索知识库。 |
| `GET` | `/v1/kb/stats` | 登录 | 获取知识库统计。 |
| `POST` | `/v1/kb/reindex` | `admin` | 异步重建知识库索引。 |
| `GET` | `/v1/journals/rank` | 登录 | 按 ISSN 或标题查询期刊分区。 |
| `GET` | `/v1/journals/tables` | 登录 | 读取已加载的期刊分区表与索引规模。 |
| `GET` | `/v1/paywall/status` | 登录 | 观测机构订阅登录态。 |
| `PUT` | `/v1/paywall/state` | `admin` | 整体替换机构登录态文件（multipart）。 |
| `DELETE` | `/v1/paywall/state` | `admin` | 清除机构登录态，返回 `204`。 |
| `GET` | `/v1/literature/search` | 登录 | 检索 Semantic Scholar/PubMed。 |
| `GET` | `/v1/literature/resolve` | 登录 | 通过必填 `ident` 查询参数解析单篇上游文献。 |
| `GET` | `/v1/literature/fulltext` | 登录 | 通过 `ident` 获取 Europe PMC 全文目录或正文。 |
| `GET` | `/v1/literature/citations` | 登录 | 通过 `ident` 获取引用该文献的文献。 |
| `GET` | `/v1/literature/references` | 登录 | 通过 `ident` 获取该文献的参考文献。 |
| `GET` | `/v1/literature/recommendations` | 登录 | 通过 `ident` 获取推荐文献。 |

## 4. 分资源详解

以下各节列出的 `401`/`403` 适用于所有受保护端点；参数结构或范围错误均可能返回 `422 validation_error`。

### 用户与会话

公开用户模型 `UserRead` 只含 `id: string`、`username: string`、`role: "user" | "admin"`、`is_active: boolean`、`created_at: UTC string`，不返回密码哈希或会话摘要。账号身份与登录响应使用 `Cache-Control: no-store`。

| 端点 | JSON 请求体 | 成功响应 |
|---|---|---|
| `POST /v1/auth/login` | `{"username":"alice","password":"<your-password>"}` | `200`，`{"access_token":"...","token_type":"bearer","expires_at":"...Z","user":UserRead}` |
| `POST /v1/auth/logout` | 无 | `204`；仅当前会话失效，再用该令牌请求得到 `401` |
| `GET /v1/auth/me` | 无 | `200 UserRead` |
| `POST /v1/auth/password` | `{"current_password":"...","new_password":"..."}` | `204`；全部会话失效，必须重新登录 |
| `POST /v1/users` | `{"username":"alice","password":"<initial-password>","role":"user"}`，`role` 可省略 | `201 UserRead`；`Location: /v1/users/{user_id}` |
| `GET /v1/users` | 无；查询参数 `limit=20`、`offset=0` | `200 Page<UserRead>`，按创建时间、ID 升序 |
| `GET /v1/users/{user_id}` | 无 | `200 UserRead` |
| `PATCH /v1/users/{user_id}` | `{"is_active":false}`；不允许其他字段 | `200 UserRead` |
| `POST /v1/users/{user_id}/password` | `{"new_password":"..."}` | `204`；全部会话失效 |

不支持公开注册、修改用户名/角色或删除账号。禁止禁用自己或最后一个活跃管理员。禁用不会删除问答或取消已提交任务；账号状态每次请求重新检查。首次管理员与遗失管理员密码的恢复在可信服务器终端执行 `picoseek create-admin <username>` / `picoseek reset-password <username>`，交互读取密码；自动化支持 `--password-stdin`。

### 4.1 Answers

#### `POST /v1/answers`

请求体为 `AnswerCreate`：

| 字段 | 类型 | 必填 | 默认 | 规则 |
|---|---|---:|---|---|
| `question` | `string` | 是 | — | 去除首尾空白后长度 `1..2000`。 |
| `engine` | `ask \| codex` | 否 | `ask` | `ask` 走确定性流水线；`codex` 交给容器内的 Codex agent（见下）。`interpret` 不接受 JSON 创建，只能走 `POST /v1/answers/interpret`。 |
| `papers` | `integer` | 否 | `8` | `1..30`。 |
| `years` | `integer \| null` | 否 | `null` | 最近年数，`1..50`；不能与 `year_from` 同时给。 |
| `year_from` | `integer \| null` | 否 | `null` | `1900..2100`。 |
| `year_to` | `integer \| null` | 否 | `null` | `1900..2100`；需要 `year_from`，且不得小于 `year_from`。 |
| `quartiles` | `integer[]` | 否 | `[]` | 每项为 `1..4`；服务端去重并升序保存。 |
| `journals` | `string[]` | 否 | `[]` | 每项去除首尾空白后长度 `1..100`。 |
| `keep_unranked` | `boolean` | 否 | `false` | 使用分区过滤时是否保留未收录期刊。 |
| `use_paywall` | `boolean` | 否 | `true` | 是否尝试机构订阅来源；容器部署不具备该能力，见“未纳入 v1”。 |
| `use_kb` | `boolean` | 否 | `true` | 是否在答案交付后后台提取事实并入库，同时允许使用既有知识。为 `false` 时不创建后台任务。 |
| `kb_hits` | `integer` | 否 | `0` | `0..20`，综合后附带的既有 KB 命中数。 |
| `max_chars` | `integer` | 否 | `28000` | 单篇送入流水线的字符预算，`4000..60000`。 |

成功返回 `202 Accepted`、响应头 `Location: /v1/answers/{answer_id}`（其中占位符取响应体的 `id`），响应体为 `Answer`，通常处于 `queued`；若 worker 已推进任务，也可能返回更新后的状态。可能错误：`forbidden`、`validation_error`、`too_many_jobs`、`upstream_unavailable`、`internal_error`。

answer 与关联 job 在同一数据库事务中提交后才入队。Redis 入队失败返回 `502 upstream_unavailable`，两者均保存为 `failed`，带失败信息与结束时间；失败答案可通过列表定位，并正常调用 `DELETE` 删除。

`engine="codex"` 时任务 `kind` 为 `codex`，由 worker 拉起容器内的 codex 运行时，工具面是 core 的 `semantic_scholar` MCP（检索 / 全文 / 本地 PDF / kb_search）。差异：

- 生效字段只有 `question`、`papers`、`years` / `year_from` / `year_to`、`quartiles`、`journals`、`use_kb`——它们被翻成检索要求写进提问，由模型转交工具，不是服务端硬过滤；
- `keep_unranked`、`use_paywall`、`kb_hits`、`max_chars` 对 codex 无效；
- 结果没有结构化 `papers` / `citations`，`body_md` 为 `null`，整篇答案只在 `answer_md` 与 `/markdown` 里，`/papers/{n}` 一律 `404`；
- 事件流只有 `agent` 一个 `stage`，工具调用以 `tool` 事件（`ToolCall`）呈现，同一 `call_id` 先后发 `started` 与终态两条；
- 成功后 `job.result` 额外带 `thread_id`，`Answer.trace` 保存本轮全部终态工具调用，`Answer.thread_id` 可用于服务端复盘会话与追问；
- `filters_label` 与 `ask` 同源（如 `年份 2023-2026; 分区 Q1/Q2`，无筛选时为 `无`）。

#### `POST /v1/answers/interpret`

`multipart/form-data`，用上传的 PDF 建立一条**文献解读**会话：

| 字段 | 类型 | 必填 | 规则 |
|---|---|---:|---|
| `files` | 文件（可重复） | 是 | 同名重复字段给出多篇；数量 `1..PICOSEEK_INTERPRET_MAX_FILES`（默认 `10`）。每个必须以 `%PDF-` 开头（否则 `422`），且不超过 `PICOSEEK_UPLOAD_MAX_MB`（否则 `413 payload_too_large`）。 |
| `question` | `string` | 视篇数 | 长度 `0..2000`。只传 1 篇时必填（没有可对照的对象）；多篇时可留空，只出综述。 |

一组文件叫一个 **docset**，落在 `PICOSEEK_DATA/docsets/<thread_id>/`，其 id 即该会话的 `thread_id`。任一文件校验失败时整个 docset 目录被清除，不留半份上传。

入队规则：

- 多篇（`>= 2`）：第一轮固定是综述，`options.mode = "overview"`、`question` 为 `梳理这组文献的组织关系与差异`、`use_kb = true`；若同时给了 `question`，再入队第二轮（`mode = "qa"`、`use_kb = false`、`parent_id` 指向第一轮、同一 `thread_id`）。worker 串行且 arq 为 FIFO，第二轮能读到第一轮的问答历史。
- 单篇：只入队一轮，`mode = "qa"`、`use_kb = true`。

成功返回 `202 Accepted`、`Location: /v1/answers/{answer_id}`，响应体是**第一轮**的 `Answer`（多篇时用 `GET /v1/answers/{id}/thread` 取到第二轮）。`filters_label` 形如 `文献解读 · 2 篇 PDF`。可能错误：`validation_error`、`payload_too_large`、`too_many_jobs`、`upstream_unavailable`、`internal_error`。

任务 `kind` 为 `interpret`，与 `ask` 的差异：

- 只有两个 `stage`：`fulltext`（逐篇解析 PDF，带 `progress` 计数）与 `synthesize`（一次模型调用），没有 `queries` / `search` / `read`；
- 有结构化 `papers` 与 `citations`，`papers[].source` 恒为 `upload`、`relevance` 恒为 `null`，`/papers/{n}` 与 `/papers/{n}/markdown` 从 docset 目录读取（`notes_md` 为空、`citations` 为空数组，`facts` 在后台入库完成后才非空）；
- `body_md` 在综述回合是「文献概览 / 组织关系 / 差异对照 / 总结」四段，在问答回合是「结论 / 证据 /（可选）局限」；
- 成功后 `job.result` 额外带 `thread_id`；首轮 `use_kb = true` 还会带 `kb_job_id`，上传件经既有 `answer_kb` 后台任务进共享文献库与知识库；
- 解析产物（`<n>.md`、`<n>_paragraphs.json`）在 docset 里复用，追问不重复解析，也不重复入库。

#### `GET /v1/answers`

查询参数：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `status` | `queued \| running \| ready \| failed \| cancelled \| null` | `null` | 精确过滤状态。 |
| `q` | `string` | `""` | 去除首尾空白后对 `question` 做子串匹配；追问行也匹配其会话根问题。 |
| `limit` | `integer` | `20` | `1..100`。 |
| `offset` | `integer` | `0` | `>=0`。 |

返回 `Page<AnswerSummary>`。普通用户仅看到自己的答案，管理员看到全部（包括历史无归属答案）。同一 codex 会话（`thread_id` 相同）只返回**最新一轮**，`n_turns` 给出该会话的回合数，`root_question` 在本行是追问时给出根问题；`thread_id` 为空的行原样返回。分页与 `total` 都按折叠后的行计数。可能错误：`validation_error`、`internal_error`。

#### `GET /v1/answers/{answer_id}`

`answer_id` 为字符串路径参数。仅本人或管理员可见；未完成时 `body_md`、完成时间等字段可为 `null`。返回 `Answer`。可能错误：`not_found`、`internal_error`。

#### `GET /v1/answers/{answer_id}/markdown`

无查询参数。answer 为 `ready` 时返回 `text/markdown; charset=utf-8` 的完整渲染稿，包含正文、参考文献和定位附录。与 CLI 文件稿不同，内部引用指向 `/v1/answers/{answer_id}/papers/{n}/markdown#p{pid}`，不会嵌入会话令牌。已有持久化稿与导入的 CLI 稿也按本次论文映射转换链接，不回写原文件。可能错误：`not_found`、`not_ready`、`internal_error`。

这些 URL 同样只允许本人或管理员访问。浏览器应拦截内部引用，带 Bearer 请求原文，渲染 Markdown 后定位 `id="p{pid}"`；直接导航不会自动附加 Bearer。不得把凭证拼进引用 URL。答案与单篇 Markdown 响应使用 `Cache-Control: no-store`。

#### `GET /v1/answers/{answer_id}/pdf`

无查询参数。answer 为 `ready` 时返回 `application/pdf`，由服务端用 Chromium 打印同一份 HTML 生成——三端下载到的是同一份排版，不依赖客户端渲染能力。内容含正文分节（结论 / 证据 / PICOS / 局限）、参考文献、引用原文附录、知识库补充与检索式；`relevance == 0` 且正文未引用的「已阅读但未采用」文献不导出。正文引用芯片是页内链接：`[n¶pid]` 跳到附录条目，`[n]` 跳到参考文献条目。

响应头 `Content-Disposition: attachment; filename="PicoSeek-{answer_id}.pdf"; filename*=UTF-8''PicoSeek-{日期}-{问题}.pdf`，中文名在 `filename*` 里，客户端应优先解码它；`Cache-Control: no-store`。渲染超时（60 秒）、Chromium 缺失或打印失败一律返回 `503 export_failed`，重试即可。可能错误：`not_found`、`not_ready`、`export_failed`、`internal_error`。

#### `GET /v1/answers/{answer_id}/papers/{n}`

`n` 为整数路径参数，最小值 `1`，对应 `Answer.papers[].n`。返回 `AnswerPaperDetail`，包括笔记、核实引文、事实、段落和全文。旧版导入的 answer 没有结构化 `papers`，此端点会返回 `not_found`。可能错误：`validation_error`、`not_found`、`internal_error`。

#### `GET /v1/answers/{answer_id}/papers/{n}/markdown`

`n` 为最小值 `1` 的整数，对应本次阅读的论文编号。返回带 `pN` 段落锚点的 `text/markdown; charset=utf-8` 原文快照，不回退到可能被后来问答覆盖的共享 `library/`。旧版答案在原文文件与参考文献编号映射存在时也可访问。未知编号、缺失快照或越界文件路径返回 `404 not_found`；非法编号返回 `422 validation_error`。

#### `DELETE /v1/answers/{answer_id}`

无请求体与查询参数，成功返回 `204 No Content`：

- answer 为活跃状态：返回 `409 conflict`，既不删除，也不请求取消。需要取消时，使用 `Answer.job_id` 调用独立取消端点。
- answer 为终态：删除数据库记录、`answers/{id}.md` 与 `answers/{id}_papers/`。删除成功后的重复请求返回 `404 not_found`。
- 文献解读：删掉会话的**最后一行**时，整个 `docsets/{thread_id}/` 目录（上传的 PDF 与解析产物）一并清除；还有其他回合时保留。
- 本地文献库 `library/` 与 KB 不随 answer 删除。
- 删除只影响这一行：会话不级联删除，指向它的追问行 `parent_id` 置为 `null`，`thread_id` 保持不变。

创建该答案的用户或管理员可操作，其他用户得到 `404`。可能错误：`not_found`、`conflict`、`internal_error`。

#### `POST /v1/answers/{answer_id}/followup`

请求体为 `FollowupCreate`：`{"question": string}`，规则同 `AnswerCreate.question`（去除首尾空白后长度 `1..2000`）。

在被追问答案所属的会话上再跑一轮：其余选项一律沿用被续接的那一轮，只换问题。父行取会话内**最后一个** `ready` 回合，而不是路径里的那一行——失败或取消的回合不阻塞后续追问。新行带 `parent_id` 与同一 `thread_id`，任务 `kind` 与被续接的引擎一致（`codex` 或 `interpret`）。文献解读的追问强制 `mode = "qa"`、`use_kb = false`：同一 docset 只入库一次。

成功返回 `202 Accepted`、响应头 `Location: /v1/answers/{new_answer_id}`，响应体为新一轮的 `Answer`。

- 答案不可见：`404 not_found`；
- 非发起人（含管理员对他人答案）：`403 forbidden`——会话归属发起人，续下去会污染对方历史；
- 答案不是智能体或文献解读引擎产出：`409 conflict`；
- 会话内仍有 `queued` 或 `running` 的回合：`409 thread_busy`；
- 会话内没有可续接的 `ready` 回合：`409 not_ready`。

其余错误同 `POST /v1/answers`：`too_many_jobs`、`validation_error`、`upstream_unavailable`、`internal_error`。

#### `GET /v1/answers/{answer_id}/thread`

无查询参数。返回 `AnswerSummary[]`：同一 `thread_id` 的全部回合，按 `created_at`、`id` 升序；`thread_id` 为空时返回只含自身的单元素数组。仅本人或管理员可见。可能错误：`not_found`、`internal_error`。

### 4.2 Jobs

#### `GET /v1/jobs`

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `kind` | `ask \| codex \| interpret \| kb_reindex \| paper_ingest \| answer_kb \| null` | `null` | 精确过滤任务种类。 |
| `status` | `queued \| running \| succeeded \| failed \| cancelled \| null` | `null` | 精确过滤状态。 |
| `limit` | `integer` | `20` | `1..100`。 |
| `offset` | `integer` | `0` | `>=0`。 |

返回 `Page<Job>`。普通用户只看到自己的 job，管理员看到全部。可能错误：`validation_error`、`internal_error`。

#### `GET /v1/jobs/{job_id}`

返回 `Job`。普通用户查询别人的 job 与不存在的 job 均返回 `404 not_found`。可能错误：`not_found`、`internal_error`。

`ask` 成功且开启 `use_kb` 时，`result.kb_job_id` 指向同一所有者的独立 `answer_kb` 任务；该任务不占用户的前台活跃任务额度，可通过同一组查询、SSE 和取消接口观察。答案完成不代表入库完成。后台任务的 `result` 在运行期间也有值，用于保存 `position`、`paper_count`、`items`、`attempt` 恢复进度；重试期间可能重新进入 `queued`。每篇最多尝试 3 次，失败、取消均不影响已交付答案。后台按篇让位给其他种类任务；不强行中断当前篇。

#### `GET /v1/jobs/{job_id}/events`

返回 `text/event-stream`。仅接受 Bearer 请求头，对应 OpenAPI `HTTPBearer`；不接受 URL 令牌。可选 `Last-Event-ID` 为两个无前导零的无符号 64 位整数，以 `-` 分隔；缺省从 `0-0` 回放。非法游标在开始流之前返回 `422`。仅本人或管理员可订阅；每次发送和空读轮询均检查当前会话，撤销或失去权限后关闭连接。协议详见“异步任务与 SSE”。可能错误：`unauthenticated`、`not_found`、`validation_error`、`internal_error`。

#### `POST /v1/jobs/{job_id}/cancel`

无请求体与查询参数。活跃 job 接受取消请求后返回 `202 Accepted`，响应体为空，`Location: /v1/jobs/{job_id}` 指向可观察状态。活跃期间重复请求仍为 `202`；进入任意终态后返回 `409 conflict`。此端点不会删除 job、answer 或结果文件。仅本人或管理员可操作，普通用户对不可见 job 得到 `404 not_found`。可能错误：`not_found`、`conflict`、`internal_error`。

### 4.3 Papers

文献库 key 按 `pmid > doi > title` 派生，非法字符折叠成下划线并截到 80 字符。`{key}` 必须匹配 `^[A-Za-z0-9._-]{1,80}$` 且不能是 `.` 或 `..`；不匹配按 `404 not_found` 处理。`.`、`..`、空串会指向库根或库外，写入入口一律 `422`。

#### `GET /v1/papers`

参数 `q: string = ""` 对标题或期刊名称做子串匹配；`limit: integer = 20`（`1..100`）；`offset: integer = 0`（`>=0`）。返回 `Page<PaperMeta>`。可能错误：`validation_error`、`internal_error`。

#### `GET /v1/papers/{key}`

返回 `PaperMeta`。可能错误：`not_found`、`internal_error`。

#### `GET /v1/papers/{key}/paragraphs`

返回 `{"items": Paragraph[]}`。可能错误：`not_found`、`internal_error`。

#### `GET /v1/papers/{key}/paragraphs/{pid}`

`pid` 为整数路径参数，返回匹配 `Paragraph.id` 的 `Paragraph`。可能错误：`validation_error`、`not_found`、`internal_error`。

#### `GET /v1/papers/{key}/facts`

返回 `{"items": Fact[]}`；文献存在但没有 facts 文件时 `items` 为空。可能错误：`not_found`、`internal_error`。

#### `GET /v1/papers/{key}/fulltext`

返回 `text/markdown; charset=utf-8`。正文内含形如 `<a id="p24">` 的段落锚点，供前端定位。可能错误：`not_found`、`internal_error`。

#### `POST /v1/papers/upload`

`multipart/form-data`，任意登录用户可用。字段 `file`（必填，首 5 字节须为 `%PDF-`，大小上限 `PICOSEEK_UPLOAD_MAX_MB`，默认 50 MB）、`title`（必填，`1..300`）、`doi`、`journal`、`year`、`authors`（选填）。

给了 `doi` 时会先向上游解析补全元数据，解析失败不阻塞入库，退回用户填写的字段。返回 `202 Accepted` 与 `kind="paper_ingest"` 的 `Job`。可能错误：`validation_error`（不是 PDF；或标题去空白后为空、为 `.`、`..` —— 这类标题无法作为文献库条目名）、`payload_too_large`、`too_many_jobs`、`upstream_unavailable`、`internal_error`。

#### `POST /v1/papers/ingest`

JSON 请求体 `{"doi": "10.…"}`（须匹配 `^10\.\S+$`，长度 `>=4`）。要求机构访问已配置且服务端装了 playwright，否则 `409 conflict`。返回 `202 Accepted` 与 `kind="paper_ingest"` 的 `Job`。可能错误：`validation_error`、`conflict`、`too_many_jobs`、`not_found`、`upstream_unavailable`、`internal_error`。

两个入口共用 `PICOSEEK_MAX_ACTIVE_JOBS_PER_USER` 额度（与问答任务同一闸门）。任务阶段为 `fulltext`（下载 + 解析）→ `kb`（抽事实 + 入库），成功事件与 `job.result` 为 `{"key", "n_paragraphs", "n_facts", "items"}`，`key` 即 `GET /v1/papers/{key}` 的键。失败码：`pdf_unreadable`、`fulltext_unavailable`、`llm_unavailable`、`timeout`、`internal_error`。

### 4.4 Knowledge Base

#### `GET /v1/kb/search`

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `q` | `string` | 必填 | 检索文本。 |
| `kind` | `fact \| paragraph \| null` | `null` | 不给时检索两类条目。 |
| `top_k` | `integer` | `8` | `1..50`。 |
| `pmid` | 可重复 `string` | `[]` | 限定 PMID；例如 `?pmid=39133485&pmid=12345678`。 |

返回 `{"query": string, "items": KbHit[]}`。首次请求会惰性加载嵌入模型，可能有明显冷启动延迟。可能错误：`validation_error`、`internal_error`。

#### `GET /v1/kb/stats`

无参数，返回 `KbStats`。可能错误：`internal_error`。

#### `POST /v1/kb/reindex`

无请求体与查询参数，需要 `admin`。返回 `202 Accepted` 与状态为 `queued` 的 `Job`（`kind="kb_reindex"`）。可能错误：`forbidden`、`upstream_unavailable`、`internal_error`。

重建与增量入库、逐篇文献保存使用同一跨进程写锁，搜索仍可读取当前完整索引。等待写锁时支持取消，最后一篇处理完成以及暂存写入完成后、发布前都会再次检查取消；未发布的取消快照不会替换线上索引。

### 4.5 Journals

#### `GET /v1/journals/rank`

查询参数 `issn: string = ""` 与 `title: string = ""` 至少有一个去除首尾空白后非空；两者都给时一并用于查询。返回 `RankResult`，未命中不是错误，而是 `found=false`、`rank=null`。可能错误：`validation_error`、`internal_error`。

#### `GET /v1/journals/tables`

无参数，返回 `RankTables`：每张已加载分区表的 `file`、`year`（从文件名推断，推不出为 `null`）、`journals`、`source`（`scimago \| custom`），以及索引规模 `issns` / `titles` 与 `loaded_at`。

`tables` 为空数组时 `quartiles` 筛选不会生效，客户端应据此提示。分区表按文件签名（名/mtime/大小）热重载：换表或新表落盘后，api 与 worker 各自在下一次查询时自动感知，无需重启。

### 4.6 Paywall

机构订阅登录态是 `paywall_fetch` 用的浏览器快照，落在 `PICOSEEK_DATA/var/sd_state.json`（可用 `SD_STATE_PATH` 覆盖），另有 `.session_storage.json` 与 `.context.json` 两份伴随文件。产品面只做**只读观测 + 管理员上传**，不在应用内代理登录；也不提供下载端点——cookie 快照等同凭据。

#### `GET /v1/paywall/status`

无参数，返回 `PaywallStatus`：`configured`（storage_state 是否存在）、`saved_at`（该文件 mtime，UTC）、`final_url`（取自 `.context.json` 的 `final_url`，缺则 `authorized_url`）、`has_session_storage`、`has_context_meta`、`playwright_available`（服务端是否装了 playwright）。可能错误：`internal_error`。

#### `PUT /v1/paywall/state`

`multipart/form-data`，需要 `admin`。字段 `storage_state`（必填，须是含 `cookies` 列表的 Playwright storage_state JSON）、`session_storage`、`context_meta`（选填，须是 JSON 对象）。返回 `200` 与最新 `PaywallStatus`。可能错误：`forbidden`、`validation_error`、`payload_too_large`（单份超过 5 MB）。

**整体替换**：三份文件由 `paywall_fetch login` 一次性产出，是一个工件而不是三个独立设置，因此没随本次上传给出的伴随文件会被删除——留着旧的会把上一次机构的 sessionStorage 与 UA 覆盖混进新 cookies，`final_url` 也会继续报着旧站点。写入顺序是「三份全部校验通过 → 原子落盘 → 删除缺省伴随文件」，所以一份非法的可选文件既不会改写也不会删掉现有登录态。

#### `DELETE /v1/paywall/state`

无请求体，需要 `admin`。删除三份文件（不存在则忽略），返回 `204`。可能错误：`forbidden`。

### 4.7 Literature

#### `GET /v1/literature/search`

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `q` | `string` | 必填 | 长度至少 1。 |
| `source` | `auto \| pubmed \| s2` | `auto` | `auto` 先查 Semantic Scholar，发生上游错误时回退 PubMed。 |
| `limit` | `integer` | `10` | `1..30`。 |
| `years` | `integer \| null` | `null` | 最近年数，`1..50`；与 `year_from` 互斥。 |
| `year_from` | `integer \| null` | `null` | `1900..2100`。 |
| `year_to` | `integer \| null` | `null` | `1900..2100`；需要 `year_from`，且不得小于它。 |
| `publication_types` | 可重复 `string` | `[]` | 文献类型过滤，例如 `?publication_types=Review&publication_types=Clinical+Trial`。 |
| `quartiles` | 可重复 `integer` | `[]` | 每项只能为 `1..4`。 |
| `journals` | 可重复 `string` | `[]` | 期刊过滤。 |
| `open_access_only` | `boolean` | `false` | 仅对 Semantic Scholar 检索生效。 |

返回 `LiteratureSearchResult`。`items` 不超过请求的 `limit`，即使 PubMed 为期刊或分区过滤扩大候选池；`total` 仍是上游命中总数，不是本地过滤后的数量。`source=auto` 回退时，结果的 `source` 为 `pubmed`，`fallback_reason` 给出 S2 失败原因；直接指定 `source=s2` 时不回退。可能错误：`validation_error`、`upstream_unavailable`、`internal_error`。

#### 标识符 `ident` 的解析

下列 literature 端点共用必填、非空的字符串查询参数 `ident`，缺失或空字符串返回 `422 validation_error`：

1. 纯数字按 PMID；
2. 匹配 `PMC\d+`（不区分大小写）时，经 Europe PMC 映射到 PMID；
3. 以 `10.` 开头时按 DOI，先查 Semantic Scholar，失败后尝试 Europe PMC 映射；
4. 其余按 Semantic Scholar paper ID。

DOI 中的斜杠属于参数值，例如 `/v1/literature/resolve?ident=10.1000/fulltext`，也可编码为 `%2F`。详情、`fulltext`、`citations`、`references`、`recommendations` 均通过 `ident` 寻址，操作名不会占用 DOI 的尾段。建议用客户端参数编码或 curl `--data-urlencode`，避免 DOI 中的 `&`、`#`、`+` 被解释为 URL 控制字符。

上游明确不存在的资源返回 `404 not_found`，无可用 XML 全文返回 `404 fulltext_unavailable`；网络故障、限流或服务故障返回 `502 upstream_unavailable`，不会伪装成空记录。DOI 解析可以由其他上游成功兜底，但存在未恢复的上游故障、无法确认资源缺失时仍返回 `502`。

#### `GET /v1/literature/resolve`

查询参数 `ident` 见上节，返回 `LiteratureRecord`。可能错误：`validation_error`、`not_found`、`upstream_unavailable`、`internal_error`。

#### `GET /v1/literature/fulltext`

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `ident` | `string` | 必填 | 非空文献标识符，解析规则见上节。 |
| `section` | `string` | `""` | 空字符串只返回章节目录和摘要，`text=null`；`all` 返回全部章节；其他值按章节标题做不区分大小写的子串匹配，并选择第一个匹配标题。 |
| `max_chars` | `integer` | `20000` | `1000..100000`；正文超出时截断并令 `truncated=true`。 |

全文只来自 Europe PMC，不下载 Semantic Scholar 的开放 PDF 作为兜底。返回 `FulltextResult`。可能错误：`validation_error`、`not_found`（章节不存在）、`fulltext_unavailable`、`upstream_unavailable`、`internal_error`。

#### `GET /v1/literature/citations`
#### `GET /v1/literature/references`
#### `GET /v1/literature/recommendations`

三者都只调用 Semantic Scholar；均接受必填非空 `ident`，以及 `limit: integer = 10`，范围 `1..50`。成功均返回 `{"items": LiteratureRecord[]}`。可能错误：`validation_error`、`not_found`、`upstream_unavailable`、`internal_error`。

### 4.8 Health

#### `GET /v1/health`

免鉴权、无参数。返回：

```json
{"status": "ok", "version": "0.1.0", "time": "2026-09-05T12:34:56Z"}
```

`version` 取当前后端版本，上例仅展示形状。

#### `GET /v1/health/ready`

免鉴权、无参数。返回 `status: "ok" | "degraded"` 与 `checks`；`checks` 固定包含 `db`、`redis`、`llm`、`kb`、`ranks`，每项形如 `{"ok": boolean, "detail": string}`。

```json
{
  "status": "degraded",
  "checks": {
    "db": {"ok": true, "detail": "ok"},
    "redis": {"ok": true, "detail": "ok"},
    "llm": {"ok": false, "detail": "model unavailable"},
    "kb": {"ok": true, "detail": "embedder=... dim=1024 items=123"},
    "ranks": {"ok": true, "detail": "scimagojr_2024.csv"}
  }
}
```

只有 DB 或 Redis 检查失败时 HTTP 状态为 `503`；LLM、KB 或分区表不可用只令总体状态为 `degraded`，以便只读浏览能力继续服务。`200` 与 `503` 均返回 `application/json` 的 `ReadinessResponse`，不是 `Problem`；两种状态已在 OpenAPI 中显式声明。

## 5. 数据模型

表中未标 `null` 的类型不会以 `null` 表示缺失。许多结果模型对旧数据兼容而带默认空值；具体 required/default 信息可由 OpenAPI codegen 保留。

### 5.1 Answer 模型

#### `AnswerCreate`

字段、默认值与校验规则见 `POST /v1/answers` 请求体表。

#### `AnswerSummary`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `string` | answer ID。 |
| `job_id` | `string \| null` | 关联的异步 job ID。 |
| `status` | `queued \| running \| ready \| failed \| cancelled` | answer 生命周期状态。 |
| `question` | `string` | 原始问题。 |
| `engine` | `ask \| codex \| interpret` | 产出该答案的引擎；历史导入数据为 `ask`。 |
| `filters_label` | `string \| null` | 已归一化的筛选条件展示文本。 |
| `n_papers` | `integer \| null` | 纳入答案的文献数。 |
| `n_fulltext` | `integer \| null` | 纳入文献中读取全文的数量。 |
| `created_at` | `string` | UTC 创建时间。 |
| `finished_at` | `string \| null` | 终态时间。 |
| `error` | `JobError \| null` | 失败信息。 |
| `n_turns` | `integer` | 所属 codex 会话的回合数；非会话行为 `1`。 |
| `root_question` | `string \| null` | 本行是追问时给出会话根问题，否则 `null`。 |

#### `Answer`

`Answer` 包含 `AnswerSummary` 的全部字段，并增加：

| 字段 | 类型 | 说明 |
|---|---|---|
| `question_en` | `string \| null` | 流水线使用的英文问题。 |
| `parent_id` | `string \| null` | 被续接的上一轮 answer ID；首轮或非 codex 为 `null`。删除父行后置为 `null`。 |
| `queries` | `string[]` | 实际执行的检索式，默认 `[]`。 |
| `options` | `object` | 创建时保存的选项，旧版导入数据可为空对象。 |
| `started_at` | `string \| null` | 开始执行时间。 |
| `papers` | `AnswerPaper[]` | 纳入结果的论文，按 `n` 编号。 |
| `body_md` | `string \| null` | 供前端渲染的综合正文；引用为裸标记，不含链接。 |
| `citations` | `Citation[]` | 正文引用对应的可定位原文。 |
| `kb_hits` | `KbHit[]` | 综合后附带的既有 KB 命中。 |
| `trace` | `ToolCall[]` | codex 引擎本轮的终态工具调用，按发生顺序；`ask` 引擎恒为 `[]`。 |

`body_md` 中的直接定位标记是 `[n¶pid]`，例如 `[2¶24]` 表示本次 answer 的第 2 篇论文、第 24 段。若段落无法解析，正文降级为 `[n]`。前端可按以下方式处理：

- 详情跳转：`/v1/answers/{answer_id}/papers/{n}`，在返回的 `paragraphs` 中定位 `id=pid`；
- 若论文已进入共享文献库，也可用其 `pmid`/key 请求 `/v1/papers/{key}/paragraphs/{pid}`，或把 `/v1/papers/{key}/fulltext` 中的 `#p{pid}` 锚点用于页面内定位；
- 不希望自行解析标记时，获取 `/v1/answers/{answer_id}/markdown` 的完整带链接稿，再以 Bearer 加载其单篇 Markdown 引用目标并保留 `pN` 锚点。

`body_md` 的正文结构由综述提示词固定为四个模块标签：`**结论 / Bottom line**`、
`**证据 / Evidence**`、`**PICOS 证据表 / PICOS table**`、`**局限 / Caveats**`（PICOS 表可能缺失）。
标签可能独占一行、以 `—` 或 `:` 接同行正文，个别情况写成 `## 标签`。前端若要分模块渲染，
按标签「前缀」识别（问题标题里出现「证据」这类字样很常见，包含匹配会误判），
并对识别不到任何标签的正文回退到整段渲染。模块的标题文字与图标应由前端固定，不要复用正文里的标签字面量。

#### `FollowupCreate`

| 字段 | 类型 | 必填 | 规则 |
|---|---|---:|---|
| `question` | `string` | 是 | 去除首尾空白后长度 `1..2000`。 |

#### `ToolCall`

| 字段 | 类型 | 说明 |
|---|---|---|
| `call_id` | `string` | 本次调用的稳定标识；`started` 与终态两条事件共用同一个值。 |
| `server` | `string` | MCP 服务名；容器内执行命令为 `shell`。 |
| `tool` | `string` | 工具名，如 `search_papers`、`read_pdf`、`kb_search`；`shell` 下为 `exec`。 |
| `status` | `started \| completed \| failed` | `started` 只出现在事件流，`Answer.trace` 只保存终态。 |
| `args` | `object` | 入参摘要，只保留标量值，字符串截断到 200 字符。 |
| `duration_ms` | `integer \| null` | 调用耗时。 |
| `error` | `string \| null` | 失败原因；命令执行失败时形如 `exit 1`。 |

同一个 `call_id` 会先收到 `status="started"`、再收到终态一条：客户端应按 `call_id` 就地替换（upsert），不要追加成两行。

#### `AnswerPaper`

| 字段 | 类型 | 说明 |
|---|---|---|
| `n` | `integer` | 论文在本次 answer 中的编号。 |
| `pmid` / `doi` / `pmcid` | `string` | 外部标识符；未知时为空字符串。 |
| `title` / `year` / `journal` / `issn` / `authors` | `string` | 论文元数据；未知时为空字符串。 |
| `quartile` | `string` | 分区值；未知时为空字符串。 |
| `rank_label` | `string` | 已格式化、可直接展示的分区标签。 |
| `source` | `pmc \| pdf \| inst \| upload \| abstract` | 流水线实际读取的内容来源，默认 `abstract`；`upload` 是文献解读里用户上传的 PDF。 |
| `relevance` | `integer \| null` | 模型判定的相关性。 |
| `n_paragraphs` | `integer` | 段落数。 |
| `n_citations` | `integer` | 抽取引文数。 |
| `n_citations_verified` | `integer` | 已核实引文数。 |

#### `AnswerPaperDetail`

包含 `AnswerPaper` 全部字段，并增加：

| 字段 | 类型 | 说明 |
|---|---|---|
| `notes_md` | `string` | 单篇阅读笔记 Markdown。 |
| `citations` | `VerifiedQuote[]` | 从笔记中抽取并核实的引文。 |
| `facts` | `Fact[]` | 原子事实。 |
| `paragraphs` | `Paragraph[]` | 自包含的段落列表。 |
| `fulltext_md` | `string` | 本次流水线保存的全文或摘要 Markdown。 |

#### `Citation`

| 字段 | 类型 | 说明 |
|---|---|---|
| `n` | `integer` | answer 内论文编号。 |
| `pmid` | `string` | PMID，未知时为空字符串。 |
| `pid` | `integer` | 被引用段落 ID。 |
| `sec` | `string` | 章节名。 |
| `page` | `integer \| null` | 页码。 |
| `text` | `string` | 该段完整原文。 |
| `quotes` | `string[]` | 落在该段中的已核实引文。 |
| `from_marker` | `boolean` | `true`：正文以 `[n¶pid]` 直接引用该段；`false`：正文只写 `[n]`，服务端补入该论文已核实的 key-finding 段落。 |

### 5.2 Job 模型

#### `Job`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `string` | job ID。 |
| `kind` | `ask \| codex \| interpret \| kb_reindex \| paper_ingest \| answer_kb` | 任务种类。 |
| `status` | `queued \| running \| succeeded \| failed \| cancelled` | job 生命周期状态。 |
| `user_id` | `string \| null` | 所有者的用户 ID；历史迁移或 CLI 导入的无归属数据为 `null`，仅管理员可见。 |
| `params` | `object` | 入队参数。 |
| `progress` | `JobProgress \| null` | 最近一次阶段/进度快照。 |
| `error` | `JobError \| null` | 失败信息。 |
| `result` | `object \| null` | ask 成功为 `{"answer_id": string}`，开启知识库时额外包含 `kb_job_id`；KB 重建为条目与论文计数，单篇入库为 `{"key", "n_paragraphs", "n_facts", "items"}`。`answer_kb` 还在运行期间保存恢复游标，见 Jobs 说明。 |
| `created_at` / `started_at` / `finished_at` | `string` / `string \| null` / `string \| null` | UTC 生命周期时间。 |

#### `JobProgress`

| 字段 | 类型 | 说明 |
|---|---|---|
| `stage` | `string` | 最近阶段。问答流水线取值见 SSE 表。 |
| `current` | `integer \| null` | 已完成数量。 |
| `total` | `integer \| null` | 总数量。 |

#### `JobError`

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | `string` | `no_papers \| nothing_relevant \| codex_failed \| pdf_unreadable \| fulltext_unavailable \| llm_unavailable \| timeout \| internal_error`。前两个只出现在 `ask` 问答任务，`codex_failed` 只出现在 `codex` 问答任务，`pdf_unreadable`、`fulltext_unavailable` 只出现在入库任务。 |
| `message` | `string` | 面向人的失败原因。 |

### 5.3 论文、知识库与分区模型

#### `Paragraph`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `integer` | 段落 ID。 |
| `sec` | `string` | 章节名。 |
| `page` | `integer \| null` | 页码。 |
| `text` | `string` | 段落原文。 |

#### `Fact`

| 字段 | 类型 | 说明 |
|---|---|---|
| `fact` | `string` | 原子事实。 |
| `fact_zh` | `string` | 中文事实文本，未知时为空。 |
| `kind` | `string` | 事实类型，默认 `finding`。 |
| `pid` | `integer \| null` | 支撑段落 ID。 |
| `sec` | `string \| null` | 支撑章节。 |
| `page` | `integer \| null` | 页码。 |
| `quote` | `string` | 支撑引文。 |
| `score` | `number` | 匹配/核实分数。 |
| `verified` | `boolean` | 是否已核实定位。 |

#### `VerifiedQuote`

| 字段 | 类型 | 说明 |
|---|---|---|
| `claimed_pid` | `integer` | 笔记声称的段落号。 |
| `pid` | `integer \| null` | 核实后的段落号。 |
| `sec` | `string \| null` | 核实后的章节。 |
| `page` | `integer \| null` | 页码。 |
| `quote` | `string` | 引文。 |
| `score` | `number` | 匹配分数。 |
| `verified` | `boolean` | 是否核实通过。 |
| `note_section` | `string \| null` | 引文在阅读笔记中的小节。 |
| `key_finding` | `boolean` | 是否属于关键发现。 |

#### `PaperMeta`

| 字段 | 类型 | 说明 |
|---|---|---|
| `key` | `string` | 本地文献库 key。 |
| `pmid` / `doi` / `pmcid` | `string` | 外部标识符。 |
| `title` / `year` / `journal` / `issn` / `quartile` / `authors` | `string` | 文献元数据。 |
| `source` | `pmc \| pdf \| inst \| upload \| abstract` | 入库时实际读到的内容：`abstract` 表示这一条只有摘要，`fulltext.md` 里也就只有摘要。客户端据此在文献库列表与详情标注「仅摘要 / 全文 · …」。 |
| `types` | `string[]` | 文献类型。 |
| `indexed_at` | `string \| null` | 入库时间。 |
| `n_paragraphs` / `n_facts` | `integer` | 段落与事实数。 |

#### `KbHit`

| 字段 | 类型 | 说明 |
|---|---|---|
| `kind` | `fact \| paragraph` | 命中类型。 |
| `pmid` / `doi` / `pmcid` | `string` | 论文标识符。 |
| `title` / `year` / `journal` / `quartile` / `source` / `authors` | `string` | 论文元数据。 |
| `pid` | `integer \| null` | 段落 ID。 |
| `sec` | `string \| null` | 章节。 |
| `page` | `integer \| null` | 页码。 |
| `text` | `string` | 命中文本。 |
| `text_zh` | `string \| null` | 中文文本。 |
| `fact_kind` | `string \| null` | 事实类型，仅 fact 命中适用。 |
| `quote` | `string \| null` | 支撑引文。 |
| `verified` | `boolean \| null` | 核实状态。 |
| `score` | `number` | 相似度分数。 |

#### `KbStats`

| 字段 | 类型 | 说明 |
|---|---|---|
| `items` | `integer` | 索引条目总数。 |
| `papers` | `integer` | 涉及论文数。 |
| `by_kind` | `object<string, integer>` | 按条目类型统计。 |
| `embedder` | `string \| null` | 嵌入器标识。 |
| `dim` | `integer \| null` | 向量维数。 |

#### `RankInfo`

| 字段 | 类型 | 说明 |
|---|---|---|
| `title` | `string` | 期刊标题。 |
| `issns` | `string[]` | 已知 ISSN。 |
| `zone` | `integer` | 分区数字。 |
| `quartile` | `string` | 如 `Q1`。 |
| `sjr` | `number \| null` | SJR。 |
| `h_index` | `string \| null` | H-index；wire 类型为字符串。 |
| `categories` | `string` | 学科类别。 |
| `top` | `boolean` | 是否为 Top。 |
| `source` | `string` | 分区数据来源。 |

#### `RankResult`

| 字段 | 类型 | 说明 |
|---|---|---|
| `query` | `{issn: string, title: string}` | 原查询。 |
| `found` | `boolean` | 是否命中。 |
| `rank` | `RankInfo \| null` | 分区详情。 |
| `label` | `string` | 已格式化、可直接展示的标签。 |

#### `RankTable` / `RankTables`

| 字段 | 类型 | 说明 |
|---|---|---|
| `tables[].file` | `string` | 分区表文件名。 |
| `tables[].year` | `integer \| null` | 从文件名推断的年份。 |
| `tables[].journals` | `integer` | 该表加载到的期刊条数。 |
| `tables[].source` | `scimago \| custom` | `scimagojr*.csv` 为 `scimago`，其余（如中科院分区导出）为 `custom`。 |
| `issns` / `titles` | `integer` | 合并后的 ISSN 与刊名索引规模。 |
| `loaded_at` | `string \| null` | 最近一次加载时刻（UTC）；文件签名变化后会刷新。 |

#### `PaywallStatus`

| 字段 | 类型 | 说明 |
|---|---|---|
| `configured` | `boolean` | storage_state 文件是否存在。 |
| `saved_at` | `string \| null` | storage_state 文件 mtime（UTC）。 |
| `final_url` | `string \| null` | 上次登录成功后落在的站点。 |
| `has_session_storage` / `has_context_meta` | `boolean` | 两份伴随文件是否存在。 |
| `playwright_available` | `boolean` | 服务端能否加载 `paywall_fetch`；为 `false` 时按 DOI 入库与付费全文下载都不可用。 |

### 5.4 Literature 模型

#### `LiteratureRecord`

| 字段 | 类型 | 说明 |
|---|---|---|
| `source` | `pubmed \| s2` | 记录来源。 |
| `id` | `string` | 来源侧主 ID。 |
| `pmid` / `pmcid` / `doi` / `s2_id` | `string \| null` | 外部标识符。 |
| `title` | `string` | 标题。 |
| `abstract` | `string \| null` | 摘要。 |
| `year` / `journal` / `issn` | `string \| null` | 出版元数据。 |
| `authors` | `string[]` | 作者。 |
| `types` | `string[]` | 文献类型。 |
| `cited_by` | `integer \| null` | 被引数。 |
| `open_access_pdf` | `string \| null` | S2 提供的开放 PDF URL。 |
| `tldr` | `string \| null` | S2 TLDR。 |
| `rank` | `RankInfo \| null` | 期刊分区。 |

#### `LiteratureSearchResult`

| 字段 | 类型 | 说明 |
|---|---|---|
| `source` | `pubmed \| s2` | 实际返回结果的来源。 |
| `total` | `integer` | 上游报告的结果总数。 |
| `items` | `LiteratureRecord[]` | 当前结果。 |
| `fallback_reason` | `string \| null` | `auto` 从 S2 回退 PubMed 时的原因。 |

#### `FulltextResult`

| 字段 | 类型 | 说明 |
|---|---|---|
| `pmcid` | `string` | 全文对应 PMCID。 |
| `citation` | `string` | Europe PMC 引用信息。 |
| `sections` | `{title: string, chars: integer}[]` | 章节目录与各章字符数。 |
| `abstract` | `string` | 摘要，缺失时为空字符串。 |
| `section` | `string \| null` | 实际选择的章节标题、`all` 或 `null`。 |
| `text` | `string \| null` | 请求了 `section` 时的正文。 |
| `truncated` | `boolean` | 是否按 `max_chars` 截断。 |

### 5.5 通用模型

#### `Page`（`Page<T>`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `items` | `T[]` | 当前页条目。 |
| `total` | `integer` | 过滤后总数。 |
| `limit` | `integer` | 本次页大小。 |
| `offset` | `integer` | 本次偏移量。 |

#### `Problem`

字段见“错误模型”。`errors` 为可选的 `object[] | null`，校验错误项的实际形状为 `{loc: string[], msg: string, type: string}`。

## 6. 异步任务与 SSE

### 6.1 生命周期

```text
POST /v1/answers
  └─ 202 + Location + Answer(status="queued", job_id=...)
       ├─ GET /v1/jobs/{job_id}/events  持续接收阶段与逐篇进度
       │    └─ succeeded / failed / cancelled 终态事件后服务端关闭流
       └─ GET /v1/jobs/{job_id}         可用于轮询或恢复状态
            └─ succeeded 后 GET /v1/answers/{answer_id}
                              GET /v1/answers/{answer_id}/papers/{n}
                              GET /v1/answers/{answer_id}/markdown
```

Answer 状态迁移为 `queued → running → ready|failed|cancelled`；对应 Job 为 `queued → running → succeeded|failed|cancelled`。

每条 SSE 包含：

- `id:` Redis Stream entry ID；
- `event:` 事件类型；
- `data:` JSON 对象。

### 6.2 事件

| event | `data` 字段 |
|---|---|
| `stage` | `{stage, status, detail}`。`stage` 为 `queries \| search \| fulltext \| read \| kb \| synthesize \| reindex \| agent`；`status` 为 `started \| finished`；`detail` 为对象，缺省为空对象。`search/finished` 包含 `candidates`、`kept`、`dropped:{year,quartile,unranked,journal}`、`papers:[{n,pmid,title,year,journal,rank_label,pmcid}]`；`read/finished` 包含 `relevant`、`total`；`agent` 是 codex 引擎唯一的阶段，`finished` 时带 `tool_calls`、`chars`。其他阶段也可在 `detail` 中报告该阶段计数或结果摘要。 |
| `progress` | `{stage, current, total, pmid?, title?}`，其中 `stage` 为 `fulltext \| read \| kb \| reindex`；`current`、`total` 为非负整数；`pmid`、`title` 为可选 `string \| null`。 |
| `log` | `{level, message}`；当前 `level` 为 `info \| warning`。只适合展示运行日志，不应据其文案驱动状态机。 |
| `tool` | `ToolCall`。只出现在 codex 引擎任务：同一 `call_id` 先 `started` 再终态，客户端按 `call_id` upsert 成一行轨迹。终态那条与 `Answer.trace` 的元素一致。 |
| `succeeded` | 问答任务为 `{answer_id}`；KB 重建与答案后台入库任务为 `{items, papers}`；单篇入库任务为 `{key, n_paragraphs, n_facts, items}`。终态。 |
| `failed` | `{code, message}`，其中 `code` 为 JobError 枚举。终态。 |
| `cancelled` | `{}`。终态。 |

`ask` 前台依次执行 `queries → search → fulltext → read → synthesize`，不再等待 `kb`；其成功事件仍仅含 `answer_id`，后台任务 ID 从 `GET /v1/jobs/{job_id}` 的 `result.kb_job_id` 读取。`kb` 阶段用于独立 `answer_kb` 和单篇入库任务，历史问答事件中也可能出现；`reindex` 阶段用于 KB 重建。单篇入库任务只用 `fulltext` 与 `kb` 两个阶段值。事件数据模型位于 OpenAPI 的 `components.schemas`，由 SSE 成功响应 `content["text/event-stream"]["x-sse-events"]` 按事件名引用。该扩展描述每帧 JSON `data`，HTTP 响应本身仍是 SSE 文本，不是 JSON 数组。Redis 发布边界与 OpenAPI 共用这些模型。

### 6.3 重连、心跳与过期

- 初次连接未给 `Last-Event-ID` 时，从 `0-0` 开始回放当前 Redis Stream 中仍保留的事件。
- 断线重连时，把最后成功处理的 SSE `id` 放进 `Last-Event-ID` 请求头；服务端从该 ID **之后**续传。
- 非法格式、前导零或超出 `0..18446744073709551615` 的游标分量返回 `422`，不会先发出 `200` 再中断流。
- 连接空闲时约每 15 秒发送一条 SSE 注释心跳。客户端应忽略注释行。
- 事件流默认保留 7 天，并受最大长度配置约束。订阅开始时以及活动订阅的空读周期（约 5 秒）会检查数据库终态；数据库连接只用于短期查询，不随 SSE 长连接持续占用。
- 数据库已终态时，先回放游标之后仍保留的事件；若终态事件发布失败、已过期，或客户端游标已越过终态事件，则补发数据库中的终态并关闭。合成事件沿用最后游标（没有历史游标时为 `0-0`），客户端不得仅因 ID 与上一条相同而丢弃终态。
- 合成 `succeeded` 从 `job.result` 按任务种类提取上述成功事件字段，不包含 `kb_job_id` 或后台恢复游标；`failed` 使用 `job.error`，`cancelled` 的 `data` 为空对象。已终态任务无需等待下一个空读周期。
- 会话在每次发送前及空读轮询时重新验证，注销、到期或禁用后停止发送并关闭。空闲流通常在下一次约 5 秒的轮询时结束；已收到的内容无法收回。非终态断开时先调用 `/v1/auth/me`，若为 `401` 则重新登录，不要携带失效令牌无限重试。

取消是异步且协作式的：`POST /v1/jobs/{job_id}/cancel` 的 `202` 只表示已写入取消请求。worker 在任务开始前、阶段边界和逐篇完成边界检查取消标记；它不会中断正在执行的 LLM 调用。客户端必须观察实际终态，不能把 `202` 当成已经取消成功；接近完成时也可能先进入 `succeeded`。

`answer_kb` 的取消还会持久化到数据库，避免 Redis 标记过期后恢复任务。即使状态已变为 `cancelled`，正在进行的单次调用仍可能完成，已写入的文献不会回滚；取消后台入库不改变 `Answer.ready`。

### 6.4 浏览器 fetch 流

以下示例使用 [`eventsource-parser`](https://github.com/rexxars/eventsource-parser) 解析 SSE（前端安装 `eventsource-parser` 3.x），不把会话令牌放进 URL。`onEvent` 处理成功后才推进游标；断线时保留游标，重新调用即可续传：

```js
import {createParser} from "eventsource-parser";

async function readJobEvents({baseUrl, jobId, token, cursor, signal, onEvent}) {
  const response = await fetch(`${baseUrl}/v1/jobs/${jobId}/events`, {
    headers: {
      Authorization: `Bearer ${token}`,
      "Last-Event-ID": cursor.id ?? "0-0",
    },
    signal,
  });
  if (!response.ok) throw await response.json();
  const parser = createParser({
    onEvent(event) {
      onEvent(event.event, JSON.parse(event.data));
      if (event.id !== undefined) cursor.id = event.id;
    },
    onError(error) { throw error; },
  });
  for await (const text of response.body.pipeThrough(new TextDecoderStream())) {
    parser.feed(text);
  }
}
```

客户端在 `succeeded` / `failed` / `cancelled` 后停止重连，离开页面可通过 `AbortController` 关闭连接。非终态断开先检查账号是否仍登录，再使用最后已处理游标重连。

命令行首次订阅与手工续传：

```bash
curl -N \
  -H 'Authorization: Bearer <access_token>' \
  'http://localhost:8765/v1/jobs/<job_id>/events'

curl -N \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Last-Event-ID: 1725537600000-0' \
  'http://localhost:8765/v1/jobs/<job_id>/events'
```

## 7. 完整 curl 流程

### 7.1 创建、观察并读取结果

先登录，将响应的 `access_token` 用于以下请求头占位符：

```bash
curl -s -X POST 'http://localhost:8765/v1/auth/login' \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"<your-password>"}'
```

创建任务；同时查看响应头可取得 `Location`：

```bash
curl -i -X POST 'http://localhost:8765/v1/answers' \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "SGLT2抑制剂对HFpEF患者有什么获益？",
    "papers": 2,
    "years": 3,
    "quartiles": [1, 2],
    "keep_unranked": false,
    "use_kb": false
  }'
```

从响应体记下 `id` 与 `job_id`，然后订阅进度：

```bash
curl -N \
  -H 'Authorization: Bearer <access_token>' \
  'http://localhost:8765/v1/jobs/<job_id>/events'
```

也可读取 job 快照：

```bash
curl -s \
  -H 'Authorization: Bearer <access_token>' \
  'http://localhost:8765/v1/jobs/<job_id>'
```

收到 `succeeded` 后读取结构化答案、某篇详情与完整渲染稿：

```bash
curl -s \
  -H 'Authorization: Bearer <access_token>' \
  'http://localhost:8765/v1/answers/<answer_id>'

curl -s \
  -H 'Authorization: Bearer <access_token>' \
  'http://localhost:8765/v1/answers/<answer_id>/papers/1'

curl -s \
  -H 'Authorization: Bearer <access_token>' \
  'http://localhost:8765/v1/answers/<answer_id>/markdown'
```

### 7.2 取消

对仍在 `queued` 或 `running` 的任务请求取消：

```bash
curl -i -X POST \
  -H 'Authorization: Bearer <access_token>' \
  'http://localhost:8765/v1/jobs/<job_id>/cancel'
```

随后通过 SSE 或 `GET /v1/jobs/{job_id}` 观察实际终态。确定不再保留终态答案时，再明确发出删除请求：

```bash
curl -i -X DELETE \
  -H 'Authorization: Bearer <access_token>' \
  'http://localhost:8765/v1/answers/<answer_id>'
```

取消请求的重试不会删除答案；活动态答案的删除请求返回 `409`，不会隐式取消。

### 7.3 只读端点

```bash
# 文献库
curl -s -H 'Authorization: Bearer <access_token>' \
  'http://localhost:8765/v1/papers?q=Lancet&limit=10&offset=0'

# KB；pmid 可重复
curl -sG -H 'Authorization: Bearer <access_token>' \
  --data-urlencode 'q=SGLT2 HFpEF 心衰住院' \
  --data-urlencode 'kind=fact' \
  --data-urlencode 'top_k=8' \
  --data-urlencode 'pmid=39133485' \
  'http://localhost:8765/v1/kb/search'

# 期刊分区
curl -sG -H 'Authorization: Bearer <access_token>' \
  --data-urlencode 'title=Lancet' \
  'http://localhost:8765/v1/journals/rank'

# 上游检索
curl -sG -H 'Authorization: Bearer <access_token>' \
  --data-urlencode 'q=SGLT2 inhibitors HFpEF' \
  --data-urlencode 'source=auto' \
  --data-urlencode 'limit=2' \
  --data-urlencode 'years=3' \
  'http://localhost:8765/v1/literature/search'

# Europe PMC 章节
curl -sG -H 'Authorization: Bearer <access_token>' \
  --data-urlencode 'ident=PMC9306514' \
  --data-urlencode 'section=Conclusion' \
  --data-urlencode 'max_chars=20000' \
  'http://localhost:8765/v1/literature/fulltext'

# 管理员异步重建 KB
curl -s -X POST -H 'Authorization: Bearer <admin_access_token>' \
  'http://localhost:8765/v1/kb/reindex'
```

## 8. 前端接入建议

1. 使用入库的 `backend/openapi.json` 生成请求客户端与 TypeScript/其他语言类型；运行中也可从 `/v1/openapi.json` 获取同一描述。SSE 帧由流解析器处理，`data` 按 `x-sse-events` 引用的模型解析。
2. 对需要实时阶段与逐篇进度的界面使用 SSE；后台恢复、列表页与不需要细粒度进度的客户端可轮询 `GET /v1/jobs/{job_id}`。无论哪种方式，都应以 job/answer 终态字段为最终事实。
3. `GET /v1/kb/search` 首次请求会加载 bge-m3；实测冷启动约 15 秒并占用约 2 GiB 内存。界面应允许首请求较长，并显示明确的加载状态。
4. `source=auto` 时 Semantic Scholar 可能因限流（常见为 429）回退 PubMed。若 `fallback_reason` 非空，可提示用户“已改用 PubMed”，不要把成功的回退结果显示为整体失败。
5. `AnswerPaper.rank_label` 与 `RankResult.label` 已由后端格式化，可直接展示；需要结构化筛选或自定义视觉样式时再读取 `quartile`/`rank`。
6. 解析 `body_md` 时保留 `[n¶pid]` 的二元定位关系，并以 `citations` 作为段落正文和核实引文的数据源；不要从显示文案反推 PMID。

## 9. 未纳入 v1

- **无通用业务限流。** 登录有用户名窗口限流，创建问答按用户限制活跃任务数；其余业务接口没有每秒/分钟配额。
- **无注册、租户或复杂角色。** 账号由管理员创建，只有 `user`/`admin`，不提供邮件找回、第三方登录、JWT 刷新令牌、组织或角色编辑。文献和衍生知识共享，不承诺严格隐私隔离。
- **容器内不支持机构订阅下载。** 镜像不包含 `core/vendor/`，因此 v1 容器服务不会通过机构订阅抓取付费全文；开放全文仅使用 Europe PMC，问答流水线在无全文时可使用摘要。本机原有 CLI 内核的机构订阅能力不属于本 API 协议。
- **全文透传不下载 OA PDF 兜底。** `/v1/literature/fulltext?ident=...` 只读 Europe PMC；`LiteratureRecord.open_access_pdf` 即使存在，也只是上游元数据。

## 10. 契约迁移

- 用户体系迁移 `0002`：停 API/worker 并备份后迁移，执行 `picoseek create-admin`。旧任务和答案保留，`user_id=null`，仅管理员可见；不会把旧 Key ID 猜测为用户。已有本地 Key 文件不读取、不删除。
- 答案会话迁移 `0003`：`answers` 增加 `parent_id`（自引用外键，`ON DELETE SET NULL`）、`thread_id` 与 `trace`（`NOT NULL DEFAULT '[]'`）。老行 `trace` 为 `[]`、`thread_id` 为 `null`，在列表里原样返回，不参与会话折叠。
- codex 引擎的工具调用由 `log` 事件（`mcp: <server>/<tool> (<status>)` 文本）改为结构化 `tool` 事件与 `Answer.trace`；`job.result` 不再带 `tool_calls`（改为 `Answer.trace`），只保留 `answer_id` 与 `thread_id`。依赖旧日志文案解析工具调用的客户端必须改读 `tool` 事件。
- codex 答案的 `filters_label` 不再是 `codex · N 次工具调用`，改为与 `ask` 同源的筛选描述。
- `GET /v1/answers` 现在按 codex 会话折叠到最新一轮：单条会话不再占多行，`total` 也按折叠计数。需要完整回合列表时用 `GET /v1/answers/{id}/thread`。
- 静态 API Key、`PICOSEEK_API_KEYS_FILE`、`PICOSEEK_AUTH_DISABLED` 和 SSE 查询令牌已移除；客户端统一登录后使用 Bearer，原生 `EventSource` 改为带请求头的流客户端。
- `Job.api_key_id` 改为 `user_id`；`PICOSEEK_MAX_ACTIVE_JOBS_PER_KEY` 改为 `PICOSEEK_MAX_ACTIVE_JOBS_PER_USER`。原来全局可读的答案及所有子资源现在仅本人或管理员可见。
- 原 `DELETE /v1/jobs/{job_id}` 已移除，取消改用 `POST /v1/jobs/{job_id}/cancel`，成功接受状态由 `204` 改为 `202`，通过 `Location` 观察任务。
- 原对活跃 answer 调用 `DELETE` 的取消行为已移除。先取 `Answer.job_id` 请求取消；`DELETE /v1/answers/{answer_id}` 只用于删除终态答案。
- 原 `/v1/literature/{ident}` 及其操作后缀路径已移除。详情使用 `/resolve?ident=...`，其余使用 `/fulltext`、`/citations`、`/references`、`/recommendations` 加 `ident` 查询参数。不保留兼容别名。
- 完整 Markdown 内部引用已改为 HTTP 快照资源，客户端需带 Bearer 加载并渲染锚点；CLI 本地稿仍使用文件相对链接。
- 重新生成 SDK，使用准确的 `application/problem+json`、`text/markdown`、就绪 `503` 和 SSE 模型定义。
