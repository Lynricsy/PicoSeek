# 交接说明

接手 PicoSeek 时先读这一页：生产跑在哪里、哪些东西不在 Git 里、哪些问题已知但没修。本页内容截至 2026-10-10，其中的运行状态会过时，接手后请按[检查清单](#接手检查清单)重新核实。

## 生产环境

| 项 | 位置 |
|---|---|
| 公网入口 | `https://openevidence.dev.ling.plus`，由宿主机上 1Panel 管理的 OpenResty 站点反代到 `http://127.0.0.1:39109`（配置：`/opt/1panel/www/sites/openevidence.dev.ling.plus/proxy/root.conf`） |
| 应用栈 | 宿主机 `/root/Projects/Ling/PicoSeek`，`.env` 中 `COMPOSE_FILE=compose.yaml:compose.qwen.yaml`；镜像 `picoseek:local`、`picoseek-web:local`、`picoseek-llm-tunnel` |
| 模型服务 | 远端 `tx-06` 的 Qwen3.8-27B，经 `llm-tunnel` 容器的两跳 SSH 隧道接入，见 [model-server.md](model-server.md) |
| 代码仓库 | `https://github.com/Lynricsy/PicoSeek`（**公开**），默认分支 `main`；推送 `apple/**` 触发 `.github/workflows/ios.yml` 产出未签名 IPA |

部署与日常运维步骤见 [deployment.md](deployment.md)。

**生产目录就是本仓库在这台机器上的工作目录**：不要在其中运行假 LLM 快速开始或其他实验性 Compose 命令，也不要用 `git worktree` 在本机开分支做验证（会软链接到生产数据）。需要在这台机器上验证时按 [development.md § 隔离验证](development.md#隔离验证) 操作。

## 不在 Git 里的状态

迁移或重建生产时，下面这些必须单独备份或交接：

| 路径 | 内容 | 说明 |
|---|---|---|
| `.env` | 生产配置，含 `NCBI_API_KEY` 等 | 模板为 `.env.example` |
| `.llm-ssh/` | 隧道专用私钥、`known_hosts`、SSH `config` | 换宿主机要重新在跳板与模型主机登记公钥 |
| `var/api.sqlite3` | 账号、会话、问答与任务记录 | SQLite，备份前先停 API 与 worker |
| `var/sd_state.json*`、`var/codex/` | 机构订阅登录态、codex 会话 | 登录态等同凭据 |
| `core/answers/`、`core/library/`、`core/kb/`、`core/docsets/`、`core/pdfs/` | 问答产物、文献库、向量索引、解读上传件 | `kb/` 可由 `library/` 重建 |
| `core/models/BAAI/bge-m3` | embedding 模型（约 2.2G） | 可从 HuggingFace 重新下载 |
| Docker 卷 `picoseek_redis-data` | 队列与事件流 | 丢失不影响已完成答案 |
| `.agent-logs/` | 145 篇开发决策与排障日志（含「为什么这样做」） | 被 `.gitignore` 排除，需要随交接另行拷贝 |

## 现有账号

生产数据库中的账号（2026-10-10 读取，密码不在任何文档中）：

| 用户名 | 角色 | 状态 | 备注 |
|---|---|---|---|
| `ling` | admin | 启用 | 原维护者 |
| `apple-e2e` | admin | 启用 | Apple 端到端测试用，**建议禁用或交接后重置密码** |
| `perf-test` | admin | 禁用 | 性能测试用 |
| `test1` | user | 启用 | 线上验收用 |
| `xy1`、`xy2`、`xy3` | user | 启用 | 分发给使用者 |

账号只能禁用不能删除；交接后应为新维护者创建管理员，并用 `picoseek reset-password` 处理旧管理员账号。

## 已知问题与技术债

按影响排序。

1. **公开仓库里有内部基础设施信息。** `docs/model-server.md` 与 `core/README.md` 含 ZeroTier 内网地址、主机路径、用户名与主机指纹；它们此前已在 README 中公开，Git 历史里也有。隧道公钥已用 `restrict`/`permitopen` 限权，但若要收紧，需要把仓库转私有或改写历史。
2. **codex 引擎没有租户隔离。** `sandbox=read-only` 不限制读取范围，模型可读到容器内任何对运行用户可读的文件；对外开放多租户前必须在容器/进程层面隔离（[architecture.md](architecture.md#codex-运行时)）。
3. **知识库跨用户共享。** 问答私有，但文献、事实与向量索引共享，且抽取以问题为上下文，不是严格的隐私隔离。
4. **CLI 的 `ask`、`codex` 子命令只认本机 LiteLLM。** `core/PicoSeek` 的 `require_services` 硬编码探测 `http://127.0.0.1:4000/v1/models`，即使 `LLM_BASE` 指向别处也会拒绝运行；`start`/`stop`/`status`/`logs` 与 `core/scripts/`、`core/litellm_config.yaml` 是早期本机 Qwen3-14B 方案，生产不使用。CLI 的 `kb`、`rank`、`paywall`、`test`、`verify` 不受影响。
5. **客户端发布签名缺失。** Flutter Android release 用 debug 签名（不能上架、不能与正式包互相覆盖）；Apple 只有 ad-hoc 签名与 CI 未签名 IPA。
6. **同一逻辑多份实现。** 答案分节有四份（后端 + 三端），筛选、引用、事件归约等纯逻辑在 Apple、Flutter、Web 各一份，靠同源测试用例约束，改动要同步（[development.md](development.md#跨端同源逻辑)）。
7. **embedding 只装了 CPU 版 PyTorch。** GPU 部署需改 `pytorch-cpu` 索引后重新 `uv lock`。
8. **遗留清理项。** 改名前的 Redis 卷 `yaopenevidence_redis-data` 作为回滚保留，确认无需回滚后可删；本机 `backend/api_keys.toml` 已不被读取；`core/.venv` 是 vendor 时代的本机环境，CLI 现默认用仓库根 `.venv`。

2026-09-21 与 2026-10-07 两次宿主机重启后站点停在 502，原因是长驻服务没有 `restart` 策略；2026-10-10 已给 `redis`、`api`、`worker`、`web` 加上 `restart: unless-stopped`。

## 接手检查清单

```bash
docker compose ps                                   # api/worker/redis/web/llm-tunnel 应在运行
curl -s http://127.0.0.1:39109/v1/health/ready      # status 应为 ok；checks.llm.detail 应提到 Qwen3.8-27B
curl -sI https://openevidence.dev.ling.plus/ | head -1
uv sync --all-packages && uv run pytest -q          # 需本机 Redis，见 development.md
```

然后用自己的账号在 Web 上完整问一次答，确认答案页的引用芯片能打开原文段落。
