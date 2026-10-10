# PicoSeek Web 工作台

浏览器端工作台：文献筛选与实时问答、段落级引用与原文阅读、文献解读（上传 PDF）、问答历史、账号设置、管理员用户管理，以及文献库、知识库和上游文献检索。技术栈为 React 19、Vite、TypeScript、Tailwind 与 shadcn/ui；后端协议见 [docs/api.md](../docs/api.md)。

## 开发

需要 Node.js 与 pnpm，以及一个在本机 `8765` 端口运行的 API（见 [docs/development.md](../docs/development.md)）。

```bash
cd frontend
pnpm install
pnpm dev          # http://localhost:5173
```

Vite 把 `/v1` 同源代理到 `http://127.0.0.1:8765`；设置 `PICOSEEK_API_PROXY` 可改写代理目标（例如指向 Compose 映射出的端口）。

| 命令 | 作用 |
|---|---|
| `pnpm gen:api` | 从入库的 `../backend/openapi.json` 生成 `src/api/schema.d.ts`（入库），保留服务端默认字段的可选性；改 API 契约后必须重跑 |
| `pnpm typecheck` | 检查应用与 Vite 配置 |
| `pnpm test` | vitest：引用、筛选、任务事件、分节等回归 |
| `pnpm lint` / `pnpm format` | oxlint / prettier |
| `pnpm build` | 生产构建到 `dist/` |

## 部署

`Dockerfile` 构建 nginx 镜像 `picoseek-web:local`，由根目录 `compose.yaml` 的 `web` 服务启动。`nginx.conf` 负责 `/v1/` 同源代理（经 Docker DNS 动态解析 `api`、关闭 SSE 缓冲）、SPA 深链回退与静态缓存；`index.html` 不缓存，避免部署后仍加载旧前端。

## 约定

- 鉴权用 Bearer 会话；SSE 用能带请求头的流式客户端，不能用原生 `EventSource`。问答仅本人和管理员可见，文献与衍生知识库共享。
- 界面为「学术编辑风」：可折叠的全局左侧导航、提问页筛选列、大屏答案与原文并排分栏。标题与正文数字使用自托管的 Noto Serif SC 与 Inter（经 `@fontsource-variable` 随构建产物分发，运行时不请求第三方 CDN）。
- 明暗主题默认跟随系统，应用内手动主题优先。筛选、手动主题、侧栏折叠状态与阅读器分栏宽度保存在当前浏览器。
- 品牌资源（登录、会话恢复、侧栏及折叠态、手机顶栏、问答首页的标识；ICO 与 PNG favicon、主屏幕图标、Web Manifest）由 `tools/generate_brand_assets.py` 生成，不要手改，见 [docs/development.md § 品牌资源](../docs/development.md#品牌资源)。
- 答案分节规则 `src/lib/answerSections.ts` 与后端、Apple、Flutter 共四份同源实现，改动需同步。
