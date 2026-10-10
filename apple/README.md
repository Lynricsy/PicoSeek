# PicoSeek Apple 客户端

与浏览器工作台功能对等的 SwiftUI 原生客户端：单一多平台 target 覆盖 iPhone、iPad 与 Mac，最低 iOS 26 / macOS 26，Swift 6 语言模式 + 严格并发。后端协议见 [docs/api.md](../docs/api.md)。

| 目录 | 内容 |
|---|---|
| `PicoSeekKit/` | 本地 SwiftPM 包：Codable 模型、`APIClient` actor、SSE 解析与全部纯逻辑（筛选归一化、引用标记、任务事件归约、Markdown 解析），可脱离 App 用 `swift test` 验证 |
| `PicoSeek/` | SwiftUI 视图与页面模型 |
| `project.yml` | XcodeGen 工程描述；生成的 `PicoSeek.xcodeproj`、`Info.plist`、entitlements 不入库 |

## 构建

需要 macOS、Xcode 26 与 XcodeGen。

```bash
brew install xcodegen
export DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer   # 或已切换的 xcode-select
cd apple && xcodegen generate
xcodebuild -project PicoSeek.xcodeproj -scheme PicoSeek -destination 'platform=macOS' build
xcodebuild -project PicoSeek.xcodeproj -scheme PicoSeek -destination 'platform=iOS Simulator,name=iPhone 17' build
cd PicoSeekKit && swift test
```

Bundle ID 为 `plus.ling.PicoSeek`。仓库内没有开发者账号，默认 ad-hoc 签名（`CODE_SIGN_IDENTITY = "-"`）；换成自己的团队时改 `project.yml` 里的签名设置。App 沙箱只申请 `network.client`。

### 没有 Mac 时：CI 出包

`.github/workflows/ios.yml` 在 `macos-26` 镜像（Xcode 26 / iOS 26 SDK）上跑 XcodeGen + `xcodebuild archive`，关闭签名后手工组 `Payload/`，产出未签名 IPA 构建产物 `PicoSeek-unsigned-ipa`。推送 `main` 上的 `apple/**` 自动触发，也可手动触发：

```bash
gh workflow run ios.yml
gh run download <run-id> -n PicoSeek-unsigned-ipa
```

产物未签名，装真机前需自行签名（`codesign` + 自己的描述文件，或 AltStore / Sideloadly 侧载时重签）。

## 行为约定

- **服务器与会话**：首屏填写服务器地址（默认 `http://localhost:8765`）与账号密码。令牌存 Keychain，`expires_at`、用户资料与服务器地址存 UserDefaults；任何受保护端点返回 401 即清会话回登录页。ATS 只放开本地网络：明文 `http://` 地址必须是 localhost 或私有网段，公网主机需 https。
- **任务进度**：走 `GET /v1/jobs/{id}/events` 的 SSE，1 秒起指数退避重连（上限 10 秒），重连前用 `/v1/auth/me` 探活；SSE 未连通时每 5 秒兜底轮询答案。
- **导出 PDF**：iOS 走系统分享面板，macOS 走保存面板；文件由服务端生成。
- **文献解读**：只做兼容——渲染解读答案、「解读」徽标与两节点进度、在会话上继续提问；创建入口只在 Web。
- **不展示开发者信息**：SSE 连接状态、运行日志、候选文献原始数据、相关性打分、嵌入模型与相似度不出现在界面上；检索式收在答案页「…」菜单的 sheet 里。

## 设计

遵循 Apple HIG：系统字体与语义色、`sidebarAdaptable` 侧栏、regular 宽度用检查器展示原文阅读器，只保留品牌深青 accent、8 色引用色板与 Q1–Q4 分区色。视觉度量集中在 `PicoSeek/Components/Surface.swift` 的 `Metrics`，Flutter 客户端以它为基准。

- 提问与追问用悬浮的大圆角玻璃输入框（`glassEffect`，发送键在框内、筛选摘要作胶囊）。玻璃只用于悬浮控件层，内容卡片一律是 `secondarySystemBackground` 平面填充。
- 答案页输入框仿 Safari 地址栏三档收放：聚焦、有草稿、提交中为完整形态，静止收成单行，向下滚动再缩成居中小药丸。输入框始终留在视图树上，焦点与草稿不会在收放间丢失。
- 历史、文献库与用户列表无限滚动；知识库与查文献用原生 `.searchable`（知识库带「全部 / 事实 / 段落」搜索范围）；滚动时收起底部 Tab 栏，关键操作带触觉反馈。
- 品牌资源：登录、会话恢复、问答首页、原生侧栏使用同一张通用图；iOS 应用图标有常规、深色与着色版本，macOS 提供完整尺寸图标，启动屏使用动态背景色。资源由 `tools/generate_brand_assets.py` 生成，见 [docs/development.md § 品牌资源](../docs/development.md#品牌资源)。

## 跨端同源

`PicoSeekKit/Tests/` 与 `flutter/test/core/` 用例同源；答案分节 `PicoSeekKit/Sources/PicoSeekKit/Logic/AnswerSections.swift` 与后端、Web、Flutter 共四份实现。改纯逻辑时同步其他端。
