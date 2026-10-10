# PicoSeek

PicoSeek 是医学文献证据问答系统：从临床或科研问题出发，由 LLM 生成检索式，经 PubMed 与 Europe PMC 检索并获取全文，按 PICOS 框架逐篇阅读，把每条引文回到原文核实，生成带段落级引用定位的综述。

**状态**：功能完整，生产部署在 `https://openevidence.dev.ling.plus`；项目正在交接，接手者请先读 [交接说明](docs/handover.md)。

## 功能

- 三种问答引擎：固定检索流水线 `ask`、自主调用工具的 `codex` agent、对上传 PDF 做综述与追问的 `interpret`
- 段落级引用：答案中的 `[n¶pid]` 可直接打开原文段落，引文经模糊匹配核实
- 文献筛选（年份、期刊分区、期刊名）、问答历史、多轮追问、答案导出 PDF
- 共享文献库与原子知识库（向量检索），答案交付后在后台入库
- 账号体系（`user` / `admin`），管理员创建账号，不开放注册
- 客户端：浏览器工作台、Apple（iPhone / iPad / Mac）、Flutter（Android / Linux / Windows），以及 `/v1` REST + SSE API 与本机 CLI

## 仓库结构

| 目录 | 内容 | 文档 |
|---|---|---|
| `core/` | 内核包 `picoseek-core`：检索、全文解析、PICOS 阅读、知识库、期刊分区；本机 CLI `./PicoSeek` | [core/README.md](core/README.md) |
| `backend/` | `picoseek-backend`：FastAPI、Alembic 迁移、arq worker、PDF 导出、后端测试 | [docs/api.md](docs/api.md) |
| `frontend/` | React 浏览器工作台与 nginx 镜像 | [frontend/README.md](frontend/README.md) |
| `apple/` | SwiftUI 多平台客户端与 `PicoSeekKit` 包 | [apple/README.md](apple/README.md) |
| `flutter/` | Flutter 客户端 | [flutter/README.md](flutter/README.md) |
| `deploy/`、`compose*.yaml`、`Dockerfile` | 镜像与 Compose 编排（含模型隧道、假 LLM 覆盖） | [docs/deployment.md](docs/deployment.md) |
| `tools/` | 品牌资源生成器 | [docs/development.md](docs/development.md#品牌资源) |
| `docs/` | 项目文档与品牌源文件 | 见下 |

## 快速开始

用确定性的假 LLM 在本机跑起整套服务，不需要 GPU，但需要 Docker Compose 和能访问 PubMed、Europe PMC 的网络。

```bash
cp .env.example .env
mkdir -p var core/pdfs
docker compose -f compose.yaml -f compose.fake-llm.yaml up -d --build
docker compose exec api picoseek create-admin admin    # 交互输入两次密码
curl -s http://localhost:8765/v1/health/ready
```

最后一条返回的 JSON 中 `checks.db`、`checks.redis`、`checks.llm`、`checks.ranks` 的 `ok` 都应为 `true`；全新环境的知识库为空，`checks.kb` 为 `false`、`status` 为 `degraded`，不影响问答。然后打开 `http://localhost:39109` 用 `admin` 登录提问。

接真实模型服务、生产部署与运维见 [docs/deployment.md](docs/deployment.md)。

## 文档

| 文档 | 读者与内容 |
|---|---|
| [docs/handover.md](docs/handover.md) | 接手者：生产环境位置、Git 之外的状态、账号、已知问题 |
| [docs/architecture.md](docs/architecture.md) | 组件、三个问答引擎、后台入库、知识库锁、PDF 导出、权限与数据目录 |
| [docs/deployment.md](docs/deployment.md) | Compose 部署、接入模型服务、账号管理、升级、就绪检查与排障 |
| [docs/configuration.md](docs/configuration.md) | 全部环境变量 |
| [docs/development.md](docs/development.md) | 本地运行、测试、修改 API 契约、跨端同源逻辑、品牌资源、提交约定 |
| [docs/api.md](docs/api.md) | `/v1` HTTP 与 SSE 协议（机器可读版为 `backend/openapi.json`） |
| [docs/model-server.md](docs/model-server.md) | 生产所用远端 Qwen 推理服务的现状、运维与变更记录 |
| [core/README.md](core/README.md) | 内核与本机 CLI、embedding 环境与调优 |

---

本项目输出为文献综述，仅供科研与教学参考，不构成医疗建议。
