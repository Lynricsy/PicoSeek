# 开发指南

在本机跑起后端、运行测试、修改 API 契约时需要同步哪些地方。各客户端的工具链与命令见各自目录：[frontend/](../frontend/README.md)、[apple/](../apple/README.md)、[flutter/](../flutter/README.md)；CLI 内核见 [core/](../core/README.md)。

## 本地运行后端

需要 Python 3.12、[uv](https://docs.astral.sh/uv/) 与一个 Redis。

```bash
uv sync --all-packages
docker run -d --name picoseek-redis -p 6379:6379 redis:7-alpine

uv run --directory backend picoseek migrate
uv run --directory backend picoseek create-admin admin   # 同一数据库只需一次
uv run --directory backend picoseek serve                # 默认 127.0.0.1:8765
# 另开终端
uv run --directory backend picoseek worker
```

本地未设 `PICOSEEK_DATA` 时数据落在 `core/` 下，数据库为 `core/var/api.sqlite3`。仓库根的 `.env` 不会被自动加载，需要的变量请自行导出（见 [configuration.md § 读取方式](configuration.md#读取方式)）。真实问答还需要可访问的模型服务；没有时用下面的假 LLM。

## 测试

```bash
uv run pytest -q
```

`testpaths` 为 `core/tests` 与 `backend/tests`。测试会对 Redis 执行 `FLUSHDB`：未设置时使用 `redis://127.0.0.1:6379/15`；若设置了 `PICOSEEK_REDIS_URL`，它必须指向本机且库号 ≥ 10，否则测试直接报错拒绝运行。

在容器里跑同一套测试：

```bash
docker compose --profile test run --rm test
```

`backend/tests/test_codex_engine.py` 会真起 codex 运行时与 MCP 子进程，不打桩。

## 无 GPU 的端到端验证

`compose.fake-llm.yaml` 启动 `backend/tests/fake_llm.py` 提供的确定性假 LLM，并让 API 与 worker 指向它。**只在全新、数据独立的开发克隆中执行，禁止在生产目录运行**：它与生产共用默认项目名、镜像标签和 `core/`、`var/` bind mount，会把线上切到假模型。

```bash
docker compose -f compose.yaml -f compose.fake-llm.yaml up -d --build
```

假 LLM 同时提供 `/v1/chat/completions`（`ask`）与 `/v1/responses`（`codex`），两个引擎都能跑通；文献检索与全文获取仍需访问 PubMed 和 Europe PMC。

验证 codex 工具闭环：提问里带 `TOOLTEST_PDF=<pdfs/ 下的文件名>`，假模型第一轮会真的调 MCP 的 `read_pdf`，第二轮把工具返回的原文抄进答案。据此可断言 `Answer.trace` 非空（`tool` 为 `read_pdf`、`status` 为 `completed`）且答案含 PDF 原文。

### 隔离验证

需要在生产宿主机上验证时（例如核对 README 快速开始），以下几点缺一不可：

1. 用 `git clone` 到新目录，不要用 `git worktree add`：本机的 worktree 扩展会把被忽略的文件（`.env`、`var/`、`core/answers/` 等）做成指向生产目录的软链接，往里写文件会直接改到生产。克隆后先跑 `find . -path ./.git -prune -o -type l -print`，有输出就先删掉这些链接。
2. 克隆放在 `/root` 下而不是 `/tmp`：Docker 守护进程看到的 `/tmp` 与登录 shell 不同，bind mount 会挂到空目录。
3. 改 `.env` 里的 `PICOSEEK_PORT`、`PICOSEEK_WEB_PORT`，用 `-p <独立项目名>` 启动，并加一个覆盖文件把 `migrate`/`api`/`worker`/`fake-llm`/`web`/`test` 的 `image:` 改成独立标签，否则构建会覆盖生产在用的 `picoseek:local`、`picoseek-web:local`。
4. 验证完 `docker compose -p <独立项目名> ... down -v`，删除独立镜像和克隆目录。

## Amp orbs

新 orb 的 `.agents/setup` 安装 Python 3.12、Redis 和 `uv.lock` 锁定的全部 workspace 与开发依赖（含 CPU PyTorch）；快照保留工具链、`.venv` 与 uv 缓存。`.agents/resume` 在激活和唤醒时经 `.amp/services.yaml` 确保 Redis 运行（只监听回环、不持久化，仅供可丢弃的开发任务）。服务异常时 `amp orb services ensure`。setup 不复制 `.env.example`、不创建账号、不下载模型。

## 修改 API 契约

协议的机器可读定义是入库的 `backend/openapi.json`，面向人的说明是 [api.md](api.md)。有意变更协议时按顺序同步：

1. 改 `backend/app/schemas/`、`routers/`，更新 `backend/tests/test_http_contract.py` 等契约测试。
2. 重新导出 OpenAPI：`uv run --directory backend picoseek export-openapi`。
3. Web：在 `frontend/` 执行 `pnpm gen:api` 重新生成 `src/api/schema.d.ts`（入库）。
4. Apple：手写 Codable 模型在 `apple/PicoSeekKit/Sources/PicoSeekKit/Models/`；Flutter：freezed 模型在 `flutter/lib/core/models/`，改后 `fvm dart run build_runner build`。
5. 更新 [api.md](api.md)；破坏性变化写进 [§10 契约迁移](api.md#10-契约迁移)。

## 跨端同源逻辑

以下逻辑在多端各有一份实现，测试用例同源，改一处要同步其余：

- 答案分节：后端 PDF 导出 + 三端，共四份（文件列表见 [architecture.md § 导出 PDF](architecture.md#导出-pdf)）。
- 筛选归一化、引用标记与色板、任务事件归约、Markdown 解析、SSE 解析：`apple/PicoSeekKit/` 与 `flutter/lib/core/`（`flutter/test/core/` 与 `apple/PicoSeekKit/Tests/` 用例同源），Web 对应 `frontend/src/lib/`。

## 品牌资源

唯一源是白底交付稿 `docs/assets/logo-source.png`。更换或修版后在仓库根执行（需要 `dev` 依赖组中的 Pillow、NumPy、SciPy 与系统 `potrace`）：

```bash
uv run python tools/generate_brand_assets.py
```

生成器先解算透明底母版 `docs/assets/logo.png`（1024 × 1024），再描摹单色矢量 `docs/assets/logo-mono.svg`（Android 主题图标用），最后输出 Web、Apple、Flutter 各端 PNG 与多尺寸 ICO。生成结果随代码入库，构建客户端不需要图形工具；不要手改生成的图片。iOS 深色图标与 Android 深色画布用深蓝底 `#0D1B2E`，其余应用图标用白底。彩色标志浅深通用，三端不按主题换图。各端资源落点见对应客户端的 README。

## 提交约定

提交信息格式为 `<type>(<scope>): <gitmoji> <subject>`，例如 `fix(core): 🐛 ...`；scope 常用 `core`、`backend`、`frontend`、`apple`、`flutter`、`native`、`docs`。一次提交只做一件事，代码、测试、OpenAPI 与文档的同一项契约变更放在同一个提交里。
