# 部署与运维

用 Docker Compose 部署整套服务（nginx 工作台、API、worker、Redis），以及部署后的账号管理、升级和排障。变量含义见 [configuration.md](configuration.md)；当前生产环境的具体位置见 [handover.md](handover.md)。

## 前提

- Docker 与 Docker Compose
- 一个容器可访问、**同时支持 Chat Completions 与 Responses API** 的 OpenAI 兼容模型服务（`ask` / `interpret` 用前者，`codex` 用后者）。没有 GPU 时可先用[假 LLM](development.md#无-gpu-的端到端验证) 跑通。
- 出网访问 PubMed、Europe PMC（Semantic Scholar、Unpaywall 可选）

## 1. 准备配置和目录

```bash
cp .env.example .env
mkdir -p var core/pdfs
```

账号存储在 API 数据库中，没有静态 API Key，也没有默认账号。

## 2. 连接模型服务

### 任意 OpenAI 兼容端点

在 `.env` 设置 `LLM_BASE`、`LLM_MODEL`、`LOCAL_QWEN_KEY`。基础 Compose 默认访问宿主机 `http://host.docker.internal:4000/v1`。

### 当前生产：经 SSH 隧道接远端 vLLM

生产接入远端 Qwen3.8-27B（服务模型名 `Qwen3.8-27B`，上下文 262144）。模型只监听远端 `127.0.0.1:29913`，经两跳 SSH 隧道接入，不向公网开放无鉴权 API。模型主机本身的部署、参数与回滚见 [model-server.md](model-server.md)。

`.env`：

```dotenv
COMPOSE_FILE=compose.yaml:compose.qwen.yaml
LLM_BASE=http://llm-tunnel:29913/v1
LLM_MODEL=Qwen3.8-27B
LOCAL_QWEN_KEY=EMPTY
PICOSEEK_JOB_TIMEOUT_S=86400
```

`compose.qwen.yaml` 增加 `llm-tunnel` 容器（`deploy/llm-tunnel.Dockerfile`），API 与 worker 等它健康后才启动。它以只读方式挂载 `.llm-ssh/`：

- `.llm-ssh/` 含专用 `id_ed25519`、固定主机指纹的 `known_hosts` 和 OpenSSH `config`，不提交，也不进镜像构建上下文；目录权限 `700`，私钥和配置 `600`。密钥只挂给隧道容器，不放进 API、worker 可读的 `var/`。
- `config` 中 `qwen-target` 经 `qwen-jump` 连接；两者都指定 `/ssh/id_ed25519`、`IdentitiesOnly yes`、`BatchMode yes`、`StrictHostKeyChecking yes` 和 `UserKnownHostsFile /ssh/known_hosts`。具体地址与用户名只写在本地配置里。
- 两台机器登记公钥前须取得授权。公钥选项为 `restrict,port-forwarding,command="/bin/false",permitopen="目的地址:端口"`：跳板只允许转发到目标的 SSH 端口，目标只允许转发到 `127.0.0.1:29913`。不放开 shell，不禁用指纹校验。
- 迁移宿主机时要重新登记专用公钥，只复制 Compose 文件不够。
- 宿主机只在 `127.0.0.1:29913` 发布隧道端口；在宿主机直接跑 Python 时把 `LLM_BASE` 改为 `http://127.0.0.1:29913/v1`。

## 3. 启动

```bash
docker compose up -d --build
docker compose exec api picoseek create-admin admin
curl http://localhost:8765/v1/health/ready
```

`create-admin` 交互读取并确认密码。就绪检查 `status` 为 `ok` 即可；各项含义见[就绪检查](#就绪检查)。

浏览器打开 `http://localhost:39109`（`PICOSEEK_WEB_PORT`）登录。nginx 把 `/v1/` 同源代理到 API、关闭代理缓冲以即时传输 SSE，并支持 `/a/<id>`、`/library/<key>` 等深链刷新，无需配置 CORS。公网入口必须在前面加 HTTPS 反向代理，不要以明文 HTTP 传输密码或令牌。

用 API 走一遍（完整流程见 [api.md §7](api.md#7-完整-curl-流程)）：

```bash
curl -s -X POST http://localhost:8765/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<your-admin-password>"}'

curl -i -X POST http://localhost:8765/v1/answers \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"question":"SGLT2抑制剂对HFpEF患者有什么获益？","papers":2,"years":3,"use_kb":false}'
```

第二条返回 `202 Accepted`，带答案位置与异步任务信息。

### 数据挂载

API 与 worker 挂载同一组宿主机目录（各目录内容见 [architecture.md § 数据目录](architecture.md#数据目录)）：

| 宿主机 | 容器 |
|---|---|
| `core/answers/` | `/data/answers/` |
| `core/library/` | `/data/library/` |
| `core/kb/` | `/data/kb/` |
| `core/docsets/` | `/data/docsets/` |
| `core/data/` | `/data/data/` |
| `core/models/` | `/data/models/`（只读） |
| `core/pdfs/` | `/data/pdfs/` |
| `var/` | `/data/var/` |

Redis 使用名为 `redis-data` 的卷（项目名前缀后为 `picoseek_redis-data`）。

### embedding 模型

知识库依赖 `core/models/BAAI/bge-m3`（约 2.2G，不入库）。下载与校验步骤见 [core/README.md §6.1](../core/README.md#61-embedding-环境kb-子命令无-gpu-亦可)；缺模型时 embedder 静默退化为 `hash-bow-v1`，第一次读写 `bge-m3` 索引时抛 `EmbedderMismatch`。CPU 编码线程数 `EMBED_THREADS` 必须按机器标定一次（[§6.2](../core/README.md#62-编码吞吐标定embed_threads--embed_batch_tokens)）。

## 账号管理

```bash
docker compose exec api picoseek create-admin <username>
docker compose exec api picoseek reset-password <username>
```

两条命令默认交互读取密码；自动化用 `--password-stdin`，不接受明文密码参数。普通用户由管理员在 Web 的用户管理页或 `POST /v1/users` 创建。用户名 3-64 位 ASCII 字母数字、下划线、横线或点，以字母数字开头，统一转小写；密码 6-128 字符，Argon2id 哈希保存。角色与会话规则见 [architecture.md § 账号与权限](architecture.md#账号与权限)。

## 升级

### 发布代码改动

Dockerfile 把源码 `COPY` 进镜像，重启容器不会带上新代码，必须重建：

```bash
docker compose up -d --build api worker   # 改了 core/ 或 backend/
docker compose up -d --build web          # 改了 frontend/
```

验证新版本真的在跑：先经 nginx 探活 `curl http://localhost:39109/v1/health/ready`（同时验证代理层），再用 `docker compose exec worker python -c "import ask, inspect; ..."` 确认容器内代码。`nginx.conf` 经 Docker DNS 动态解析 `api`，单独重建 API 不需要重启 `web`。

改了 `.env` 后要 `docker compose up -d --force-recreate`，否则运行中的容器仍持旧环境。注意 Compose 插值时**当前 shell 的环境变量优先于 `.env`**：在已导出 `LLM_BASE` 等变量的终端里执行 `up` 会覆盖 `.env`。

### 数据库迁移

`migrate` 服务在每次 `up` 时执行 Alembic 升级（`backend/alembic/versions/`）。升级前停 API 与 worker 并备份 `var/api.sqlite3` 与 `core/` 下的数据目录。

从静态 API Key 版本升级（迁移 `0002`）：旧任务和答案保留，`user_id` 为 `null`，仅管理员可见；迁移后执行 `create-admin`。`PICOSEEK_MAX_ACTIVE_JOBS_PER_KEY` 改名为 `PICOSEEK_MAX_ACTIVE_JOBS_PER_USER`。协议层面的全部变化见 [api.md §10](api.md#10-契约迁移)。

### 导入 CLI 时代的答案

API 不会自动扫描 CLI 生成的 `answers/<时间戳>.md`。需要时执行一次（幂等，不会补造结构化逐篇数据）：

```bash
docker compose run --rm api picoseek import-answers
```

## 就绪检查

`GET /v1/health/ready` 返回五项检查：

| check | 检查内容 |
|---|---|
| `db` | 数据库能否执行 `SELECT 1` |
| `redis` | Redis 能否响应 `PING` |
| `llm` | LLM `/models` 是否可达并包含 `LLM_MODEL` |
| `kb` | 索引头（`kb/index.npz`，旧布局为 `kb/info.json`）是否存在且非空，并报告 embedder、维度和条目数 |
| `ranks` | `data/journal_ranks/` 下是否加载到期刊分区表 |

任一项失败时 `status` 为 `degraded`。只有 `db` 或 `redis` 失败才返回 HTTP `503`；LLM、KB 或分区表失败仍返回 `200`，因为只读浏览仍可用。

## 排障

| 现象 | 原因与处理 |
|---|---|
| 宿主机重启后站点 502 / 打不开 | `compose.yaml` 的服务没有 `restart` 策略，只有 `llm-tunnel` 会自启。执行 `docker compose up -d`，再查就绪检查。 |
| 改了源码但线上行为没变 | 镜像是 `COPY` 构建的，见[发布代码改动](#发布代码改动)。 |
| 就绪检查 `llm` 失败，隧道容器 healthy | 隧道连接可能停滞：`docker compose restart llm-tunnel`。报 `administratively prohibited` 说明跳板 `permitopen` 未放行目标。 |
| KB 索引与 embedder 不一致（`EmbedderMismatch`） | 管理员调用 `POST /v1/kb/reindex`，或在 `core/` 下 `./PicoSeek kb reindex`。不要用一套 embedding 读另一套索引。 |
| 首次 KB 检索很慢 | bge-m3 惰性加载，实测约 15 秒、约 2 GiB 内存。 |
| 机构订阅下载不生效 | 看 `GET /v1/paywall/status`：`playwright_available=false` 是镜像里没有浏览器；`configured=false` 是还没上传登录态——在有桌面的机器上跑 `core/PicoSeek paywall login`，再经 Web `/admin/institution` 上传三份文件。 |
| Semantic Scholar 返回 429 | `source=auto` 的文献搜索会自动回退 PubMed，并在响应中给出原因；配置 `S2_API_KEY` 可缓解。 |
| 任务看似串行 | `PICOSEEK_WORKER_MAX_JOBS` 默认 `1`，是单 GPU 的预期配置。扩展多个 worker 副本时必须挂载同一份数据卷。 |
| 需要 GPU 版 PyTorch | workspace 把 `torch` 固定到 CPU wheel 索引。把根 `pyproject.toml` 与 `core/pyproject.toml` 的 `pytorch-cpu` 索引改为对应 CUDA 索引，再 `uv lock`。 |
