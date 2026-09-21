# PicoSeek Flutter 客户端

面向 Android / Linux / Windows 的原生客户端，与 `frontend/`（浏览器）和 `apple/`（iPhone / iPad / Mac）功能对等：登录、提问与筛选、答案页实时流水线进度（SSE）+ 段落级引用 + 原文阅读器、问答历史、文献库与文献详情、知识库检索与重建、上游文献检索与全文预览、账号设置、管理员用户管理。

后端协议见仓库根的 [docs/api.md](../docs/api.md)。

## 工具链

| 项 | 版本 / 说明 |
|---|---|
| Flutter | `3.47.2` stable（Dart 3.13.2），由 `.fvmrc` 固定；命令一律 `fvm flutter …` / `fvm dart …` |
| 平台 | `android`、`linux`、`windows`（iOS/macOS 归 `apple/`，Web 归 `frontend/`） |
| Linux 桌面构建 | `clang`、`cmake`、`ninja`、`pkg-config`、`gtk3`、`libsecret` |
| Android 构建 | Android SDK（platform 37 + build-tools 36 起）+ JDK 17；`android/app/build.gradle.kts` 固定 `compileSdk = 37`（插件 AAR 已按 37 发布，模板默认 36 会在 `checkDebugAarMetadata` 失败） |

```bash
cd flutter
fvm install                                    # 按 .fvmrc 安装固定版本
fvm flutter config --android-sdk /opt/android-sdk \
                   --jdk-dir /usr/lib/jvm/java-17-openjdk
fvm flutter pub get
```

Gradle 不支持较新的系统默认 JDK，因此必须显式指向 JDK 17；`flutter doctor -v` 里 Android toolchain 和 Linux toolchain 都应为绿色。

## 字体

标题使用裁剪后的 Noto Serif SC，正文数字与拉丁字符使用 Inter，中文正文走系统无衬线回退。产物已入库（`assets/fonts/`），运行时不请求任何第三方 CDN。需要更新字体时执行：

```bash
tool/fetch_fonts.sh    # 需要 curl、unzip、uvx（fonttools）
```

脚本会下载 Noto Serif SC 可变字体并用 `pyftsubset` 裁到「拉丁 + 标点 + CJK 基本区 + 全宽符号」（保留 `wght` 轴），再从 Inter 发布包取 `InterVariable.ttf`，同时写入 `assets/fonts/OFL.txt`。

## 品牌资源

标识（字母 P 与放大镜里的展开书页）的唯一源是仓库根的 [docs/assets/logo-source.png](../docs/assets/logo-source.png)，位图与图标由 `tools/generate_brand_assets.py` 生成后入库，客户端不引入 SVG 运行库：

```bash
uv run python tools/generate_brand_assets.py   # 需要 potrace
```

| 位置 | 用途 |
|---|---|
| `assets/brand/logo.png` + `2.0x/`、`3.0x/` | Dart 界面的品牌标识，`pubspec.yaml` 只声明 1x 路径，密度变体按目录约定解析 |
| `assets/brand/app-icon.png` | Linux 窗口图标（运行时按可执行文件相对路径从 bundle 读取） |
| `android/app/src/main/res/mipmap-{density}/ic_launcher.png` | 旧启动器的 legacy 图标 |
| `android/app/src/main/res/drawable-{density}/ic_launcher_foreground.png` | 自适应图标前景（透明底，浅深共用） |
| `android/app/src/main/res/drawable/ic_launcher_monochrome.xml` | Android 13+ 主题图标（生成器从 `docs/assets/logo-mono.svg` 转矢量） |
| `android/app/src/main/res/drawable-{density}/brand_splash.png` | 启动画面图形（透明底，浅深共用） |
| `windows/runner/resources/app_icon.ico` | 可执行文件与窗口图标（`Runner.rc` 的 `IDI_APP_ICON`） |

界面接入统一走 `lib/shared/widgets/brand_logo.dart`：`BrandLogo` 固定宽高渲染同一张透明底位图（标志本身浅深通用，不按主题换图），`BrandLockup` 是「标识 + 文字」横排组合（语义由文字承载，图形不重复朗读）。落点为登录页（72 px 标识竖排在标题上方，故直接用 `BrandLogo`）、会话恢复启动页、侧栏页头（展开为组合标记、折叠为竖排标识）、手机顶栏 `leading`（详情路由改显示返回键）、提问页 Hero 顶部的 40 px 标识。列表与导航里的书本图标是功能图标，保持不变。

Android 侧：`mipmap-anydpi-v26/ic_launcher.xml` 给出自适应图标，前景浅深共用，背景取 `@color/brand_canvas`（浅 `#FFFFFF` / 深 `#0D1B2E`，由 `values{,-night}/colors.xml` 按主题解析）；`drawable/launch_background.xml` 是 API 31 以下的启动窗口背景；`values-v31/styles.xml` 与 `values-night-v31/styles.xml` 用 `windowSplashScreenBackground` + `windowSplashScreenAnimatedIcon` 接管 Android 12+ 的系统启动画面，避免冷启动闪默认图标（两者只差窗口主题父类，深色资源由颜色限定符提供）。

桌面侧：Windows 由 `Runner.rc` 把 ICO 编进可执行文件，窗口类图标沿用 `IDI_APP_ICON`。Linux 用 `gtk_window_set_default_icon_from_file()` 读 bundle 内的 `data/flutter_assets/assets/brand/app-icon.png`（按 `/proc/self/exe` 定位，不依赖工作目录；读不到只告警，不影响启动）。构建还会在 bundle 的 `share/applications/` 与 `share/icons/hicolor/512x512/apps/` 输出 `.desktop` 和应用图标，应用 ID 均为 `plus.ling.picoseek`，供 Wayland/GNOME 等桌面匹配；打包方需将这些目录安装到系统或用户的标准位置，并让 `picoseek` 可执行文件处于 `PATH` 中。构建本身不会修改系统桌面配置。Android 应用标签及 Windows/Linux 窗口标题统一显示 `PicoSeek`，包名与可执行文件名不变。

## 代码生成

模型（freezed + json_serializable）与 provider（riverpod_generator）依赖生成代码，`*.g.dart` / `*.freezed.dart` **入库**（与 `frontend/src/api/schema.d.ts` 的策略一致）：

```bash
fvm dart run build_runner build      # 一次性生成
fvm dart run build_runner watch      # 开发期监听
```

`build_runner` 2.16 已移除 `--delete-conflicting-outputs`，不要再传。JSON 字段映射统一在 `build.yaml` 配置（`field_rename: snake`、`include_if_null: false`），模型里不再逐个标注。

## 检查与测试

```bash
fvm flutter analyze                  # 0 issues（含 riverpod_lint 原生 analyzer 插件）
fvm flutter test                     # 单元测试
```

`analysis_options.yaml` 用 Dart 3.9+ 的新插件系统在顶层 `plugins:` 声明 `riverpod_lint`（旧的 `analyzer.plugins:` 列表写法已废弃），插件 lint 需逐条在 `diagnostics:` 里开启。

测试集中在纯逻辑与协议边界：

- `test/core/api/`：SSE 解析状态机、查询串编码、Problem 错误映射、401 会话失效、事件流请求头。
- `test/core/logic/`：筛选归一化与请求体、引用标记与色板、任务事件归约、Markdown 解析、引文高亮。
- `test/core/models/`：线上字段映射（snake_case、未知枚举回落、null 省略）。
- `test/core/session/`：服务器地址校验（明文 http 仅限本地网络）。
- `test/features/answer/`：SSE 断线重连退避与探活（`fake_async`）。
- `test/shared/widgets/`：滚动收起状态机（向下滚隐藏 / 向上滚与触顶触底恢复 / 短页面不收起）与悬浮提问框（内容底部避让、收起时滑出视口）。

`test/core/` 的用例集与 `apple/PicoSeekKit/Tests/` 同源，改动纯逻辑时两边应同步；`test/shared/`、`test/features/` 下的是 Flutter 独有的组件行为回归。

## 设计系统

视觉基准是 iOS 版的 `apple/PicoSeek/Components/Surface.swift`；令牌集中在 `lib/app/theme/tokens.dart`，组件外观集中在 `lib/app/theme/app_theme.dart` 的 `buildTheme`，页面不再手写 `Container + BoxDecoration`。

| 原语 | 位置 | 用途 |
|---|---|---|
| `PicoSeekCard` | `shared/widgets/surface.dart` | 内容卡：圆角 16、无描边、两层柔和阴影；`tint` 参数给语义提示卡（底 10% + 描边 28%、无阴影），`elevated: false` 给次级卡 |
| `GlassPanel` | 同上 | 悬浮控件层材质：`BackdropFilter` 模糊 + 半透明卡片色 + 悬浮阴影 |
| `FilterGroup` | 同上 | 筛选面板的一组控件（一张卡） |
| `PageBody` | `shared/widgets/page_header.dart` | 页面统一约束：最大宽 720、水平内边距 16，并只消费一次底部保留区 |
| `ScrollChromeController` / `ScrollChrome` | `shared/widgets/scroll_chrome.dart` | 「随滚动收起的底部 chrome」状态，由 `AppShell` 提供，提问框与手机底部导航栏共用一个实例 |
| `FloatingComposerHost` | `shared/widgets/floating_composer.dart` | 悬浮提问框宿主：把 composer 实测高度写进 body 的 `MediaQuery.padding.bottom`，内容自动避让 |
| `showAdaptiveSheet` | `shared/widgets/adaptive_sheet.dart` | 紧凑宽度用底部 sheet、其余从右侧滑入抽屉；筛选与全文面板共用 |
| `Pager` | `shared/widgets/pagination.dart` | 换页器：`Pager.isUseful` 判断有无页可换（`total > limit \|\| offset > 0`），无页可换时不渲染。**必须跟着列表内容滚动，不做固定底栏**——它只有翻到列表尽头才有用，钉住会永久占掉一条屏幕高度；列表用 `itemCount + 1` 把它挂在末尾 |

两条容易踩的框架细节，改主题时注意：

- `ChipThemeData.labelStyle` 会被 `RawChip` 的 `labelStyle.merge(widget.labelStyle)` 抹平，`WidgetStateTextStyle` 的字段会全部丢失；框架只对 `labelStyle.color` 解析 `WidgetStateProperty`，所以选中态颜色必须写成 `WidgetStateColor`。
- `FloatingComposerHost` 所在的 `Row` 必须用 `CrossAxisAlignment.stretch`。默认的 `center` 会给子项松高度约束，`Stack` 于是收缩到内容高度、内容被整体垂直居中。

面向用户的界面刻意不展示 SSE 连接状态、运行日志、检索式（收在答案页「⋯」菜单的独立面里）、相似度打分、嵌入模型与维度、后端错误 message 这类开发者信息；这些只保留在 Web 端与后端日志。

## 构建

```bash
fvm flutter build linux --release
ANDROID_HOME=/opt/android-sdk fvm flutter build apk --debug
# arm64 发布包（真机安装用这条）
ANDROID_HOME=/opt/android-sdk fvm flutter build apk --release --split-per-abi --target-platform android-arm64
```

APK 产物在 `build/app/outputs/flutter-apk/`。Windows 目录随 `flutter create` 入库，但只能在 Windows 主机上构建（本仓库的 CI/开发机为 Linux）。

构建 arm64 包必须带 `--split-per-abi`：只给 `--target-platform android-arm64` 时仅 Flutter 引擎与 AOT 产物受限，插件的 `armeabi-v7a`/`x86_64` 原生库仍会被打进同一个 APK，装到 32 位设备会因缺 `libflutter.so` 崩溃。`--split-per-abi` 下 `versionCode` 由 Flutter 自动加 `1000 * ABI_VERSION`（arm64 为 `2001`）。

`release` 目前用 debug 签名（仓库无发布证书，见 `android/app/build.gradle.kts`），产物可安装但不能上架，也无法与正式签名的版本互相覆盖升级。

## 端到端冒烟

`integration_test/e2e_smoke_test.dart` 需要一套可用的后端，通过 `--dart-define` 提供凭据后才会执行，否则整体跳过：

```bash
fvm flutter test integration_test/e2e_smoke_test.dart -d linux \
  --dart-define=PICOSEEK_E2E_API=http://127.0.0.1:18766 \
  --dart-define=PICOSEEK_E2E_USER=admin \
  --dart-define=PICOSEEK_E2E_PASSWORD=***
```

流程：登录 → 提问（`papers=1`，关闭知识库）→ 等待终态（≤ 10 分钟）→ 断言正文出现引用芯片 → 点击芯片 → 断言阅读器显示带高亮的段落。

## 目录结构

```text
lib/
  main.dart                  # 读偏好 → ProviderScope（4xx 不自动重试）→ App
  app/                       # MaterialApp.router、主题令牌与 ThemeData、路由、答案版本号
  core/api/                  # ApiClient（REST + SSE）、错误映射、查询串编码、端点封装
  core/models/               # freezed 数据类，对齐 /v1 契约
  core/logic/                # 纯逻辑：筛选、引用、任务事件归约、Markdown、引文高亮
  core/session/              # 偏好、令牌存储、地址校验、会话状态机
  features/<page>/           # 按页面分包：ask / answer / reader / history / library / kb / literature / account / admin / shell / login
  shared/                    # 跨页组件、外链、格式化
test/                        # 与 lib 同构的单元测试
integration_test/            # 端到端冒烟
```

## 平台行为差异

- 令牌存储：Android 走 EncryptedSharedPreferences，Linux 依赖桌面会话的 Secret Service（libsecret），Windows 用 DPAPI。Linux 上先看 `DBUS_SESSION_BUS_ADDRESS` / `SECRET_SERVICE_ADDRESS`：没有可用会话（CI、容器、Xvfb）时直接走明文偏好——libsecret 此时只发 GLib 警告，插件转不成 Dart 异常，硬写会终止进程。回退状态在账号页显示「当前平台无安全存储，令牌以明文保存」，不阻塞登录。
- 明文 `http://` 只允许 localhost、`.local`/`.localhost`、`::1` 与私有网段；公网地址必须 `https://`。
- 动效只用框架内置能力，并尊重系统「减弱动效」设置。
