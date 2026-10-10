# 配置参考

本页列出代码读取的全部环境变量。模板见仓库根的 [`.env.example`](../.env.example)。

## 读取方式

- **Compose**：`compose.yaml` 用 `env_file: .env` 把 `.env` 整体喂给 `api`、`worker`、`migrate`，并在 `environment:` 中固定容器内路径（下表「Compose 固定值」列）。插值时当前 shell 的同名变量优先于 `.env`。
- **后端 `PICOSEEK_*`**：由 `backend/app/config.py` 的 `Settings`（pydantic-settings）读取，额外读取**当前工作目录**下的 `.env`；`uv run --directory backend ...` 时工作目录是 `backend/`，不会读到仓库根的 `.env`。
- **内核与其他变量**：只读 `os.environ`，没有 dotenv 加载。本机直接运行时需要自己导出，例如 `set -a && . ./.env && set +a`（注意 `.env` 里的 `LLM_BASE` 若指向容器主机名 `llm-tunnel`，宿主机上要改成 `http://127.0.0.1:29913/v1`）。

## Compose

| 变量 | 默认值 | 作用 |
|---|---|---|
| `COMPOSE_FILE` | 未设置（只用 `compose.yaml`） | 生产设为 `compose.yaml:compose.qwen.yaml` 以启用 SSH 模型隧道 |
| `PICOSEEK_PORT` | `8765` | API 的宿主机映射端口（同时是 `picoseek serve` 的默认端口） |
| `PICOSEEK_WEB_PORT` | `39109` | nginx 工作台的宿主机端口 |

## 后端：`PICOSEEK_*`

与 `backend/app/config.py` 的 `Settings` 字段一一对应。

| 变量 | 代码默认值 | Compose 固定值 | 作用 |
|---|---|---|---|
| `PICOSEEK_REDIS_URL` | `redis://127.0.0.1:6379/0` | `redis://redis:6379/0` | arq 队列、任务事件流与取消标记 |
| `PICOSEEK_DATABASE_URL` | 空 → `sqlite:///<PICOSEEK_DATA>/var/api.sqlite3` | `sqlite:////data/var/api.sqlite3` | SQLAlchemy URL；含 `%` 时按原 URL 填写 |
| `PICOSEEK_SESSION_TTL_S` | `604800` | | 登录会话固定有效期（秒），> 0 |
| `PICOSEEK_LOGIN_MAX_ATTEMPTS` | `10` | | 单个用户名在窗口内的最大登录请求数（含成功），> 0 |
| `PICOSEEK_LOGIN_WINDOW_S` | `300` | | 登录限流窗口（秒），> 0 |
| `PICOSEEK_CORS_ORIGINS` | `[]` | | 允许的 CORS origin，逗号分隔或 JSON 数组；同源部署不需要 |
| `PICOSEEK_HOST` | `127.0.0.1` | | `picoseek serve` 监听地址（镜像 CMD 显式传 `0.0.0.0`） |
| `PICOSEEK_PORT` | `8765` | | `picoseek serve` 端口 |
| `PICOSEEK_WORKER_MAX_JOBS` | `1` | | 单个 worker 同时执行的任务数 |
| `PICOSEEK_JOB_TIMEOUT_S` | `1800` | | worker 任务超时；生产设 `86400`，不要设 `0` |
| `PICOSEEK_MAX_ACTIVE_JOBS_PER_USER` | `2` | | 每用户 queued/running 任务上限，多个会话共享；文献解读要求 ≥ 2 |
| `PICOSEEK_EVENTS_TTL_S` | `604800` | | Redis 事件流与取消标记保留秒数 |
| `PICOSEEK_EVENTS_MAXLEN` | `2000` | | 每个任务事件流的近似最大条数 |
| `PICOSEEK_UPLOAD_MAX_MB` | `50` | | 单个上传 PDF 上限（MB），上传入库与文献解读共用 |
| `PICOSEEK_INTERPRET_MAX_FILES` | `10` | | 一次文献解读最多几篇 PDF |

## 数据与模型服务

| 变量 | 默认值 | 作用 |
|---|---|---|
| `PICOSEEK_DATA` | `core/`（容器内 `/data`） | 运行期数据根目录，布局见 [architecture.md § 数据目录](architecture.md#数据目录) |
| `LLM_BASE` | `http://127.0.0.1:4000/v1`（Compose 默认 `http://host.docker.internal:4000/v1`） | OpenAI 兼容 API 根地址；须同时提供 Chat Completions 与 Responses API |
| `LLM_MODEL` | `qwen3-14b` | 模型名；就绪检查验证它出现在 `/models` 中 |
| `LOCAL_QWEN_KEY` | `sk-123456` | 调用模型服务的 Bearer key |

## 知识库 embedding

| 变量 | 默认值 | 作用 |
|---|---|---|
| `EMBED_MODEL` | `<PICOSEEK_DATA>/models/BAAI/bge-m3` | sentence-transformers 模型目录；末级目录名决定 embedder 名称 |
| `EMBED_BACKEND` | `torch` | `torch` 或 `onnx`；两者向量不兼容，切换后必须重建索引 |
| `EMBED_THREADS` | `0`（沿用 torch 默认 = 逻辑核数） | CPU 编码线程数，必须按机器标定 |
| `EMBED_BATCH_TOKENS` | `1024` | 单批 padding 后的 token 上限 |

标定方法与 ONNX 后端的代价见 [core/README.md §6.2–6.3](../core/README.md#62-编码吞吐标定embed_threads--embed_batch_tokens)。

## 文献上游

| 变量 | 默认值 | 作用 |
|---|---|---|
| `NCBI_API_KEY` | 空 | PubMed 配额从 3 提升到 10 req/s |
| `S2_API_KEY` | 空 | Semantic Scholar；无 key 时常被 429，自动回退 PubMed |
| `S2_TIMEOUT` | `30` | 文献上游 HTTP 超时（秒） |
| `UNPAYWALL_EMAIL` | `picoseek@example.com` | 调用 Unpaywall 时的联系邮箱 |

## 机构订阅下载

| 变量 | 默认值 | 作用 |
|---|---|---|
| `SD_STATE_PATH` | `<PICOSEEK_DATA>/var/sd_state.json` | 登录态文件；同目录另有 `.session_storage.json` 与 `.context.json` 伴随文件 |
| `PAYWALL_MAX_PER_RUN` | `5` | 每次问答最多尝试的机构订阅下载数 |
| `PAYWALL_MIN_DELAY` / `PAYWALL_MAX_DELAY` | `4` / `9` | 两次下载之间的随机间隔（秒） |
| `PW_CHANNEL` | 空 | 设为 `chrome` 时用系统 Chrome 代替 Playwright 自带 Chromium |
| `PAYWALL_DEBUG` | 空 | 非空时输出调试信息 |
| `PLAYWRIGHT_BROWSERS_PATH` | 镜像内 `/ms-playwright` | Playwright 浏览器目录（Dockerfile 设置） |

## codex 引擎

| 变量 | 默认值 | 作用 |
|---|---|---|
| `CODEX_HOME` | `<PICOSEEK_DATA>/var/codex`（镜像内 `/data/var/codex`） | codex 会话与运行状态目录 |
| `CODEX_MCP_STARTUP_TIMEOUT_S` | `60` | 等待 MCP 工具服务启动的秒数 |
| `CODEX_MCP_TOOL_TIMEOUT_S` | `180` | 单次 MCP 工具调用超时 |

## 仅本机 CLI

| 变量 | 默认值 | 作用 |
|---|---|---|
| `PICOSEEK_ENV` | 仓库根 `.venv` | `core/PicoSeek` 与 `core/scripts/*.sh` 使用的 Python 环境 |
| `QWEN3_14B` / `QWEN3_4B` | `core/models/Qwen/Qwen3-14B`、`…/Qwen3-4B` | `./PicoSeek start` 本机起模型时的权重目录 |

## 开发与测试

| 变量 | 默认值 | 作用 |
|---|---|---|
| `PICOSEEK_API_PROXY` | `http://127.0.0.1:8765` | `frontend/` 的 Vite 开发服务器把 `/v1` 代理到哪里 |
| `PICOSEEK_REDIS_URL`（测试） | `redis://127.0.0.1:6379/15` | 测试会 `FLUSHDB`；显式设置时必须指向本机且库号 ≥ 10，否则测试拒绝运行 |
