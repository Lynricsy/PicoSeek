# PicoSeek 内核与本机 CLI

`core/` 是 `picoseek-core` 包：检索、全文获取与解析、PICOS 逐篇阅读、引文核实、原子知识库与期刊分区。HTTP 后端与 worker 直接 import 这里的模块；`./PicoSeek` 是同一套代码的本机命令行入口。整体架构见 [docs/architecture.md](../docs/architecture.md)，部署见 [docs/deployment.md](../docs/deployment.md)。

**问一个临床问题 → 自动检索 PubMed / Europe PMC → 下载全文 → 模型逐篇阅读 → 输出带编号引用、可核对的答案。**

```
问题 ──> ask.py 流水线 ──> PubMed / Europe PMC 检索
                       ├─> 全文下载：PMC XML → Unpaywall PDF → 机构订阅 PDF（可选）
                       ├─> 模型逐篇阅读做笔记（LLM_BASE，CLI 默认 LiteLLM :4000）
                       └─> 综合成答案  answers/<时间戳>.md  +  answers/<时间戳>_papers/（原文 + 笔记）

Codex CLI ──(Responses API)──> LiteLLM :4000 ──> vLLM :8000
   └── MCP: semantic_scholar_mcp.py（Semantic Scholar / PubMed / Europe PMC 全文 / 本地 PDF）
```

> CLI 的 `ask`、`codex` 子命令在运行前固定探测 `http://127.0.0.1:4000/v1/models`，即要求本机有 LiteLLM 代理（`./PicoSeek start` 会连同 vLLM 一起拉起，这是早期本机 Qwen3-14B 方案，生产不使用）。`kb`、`rank`、`paywall`、`test`、`verify` 不需要模型服务。

## 1. 快速开始

先在仓库根执行 `uv sync --all-packages` 创建 `.venv`，然后：

```bash
cd core
./PicoSeek start          # 启动 vLLM + LiteLLM（tmux 后台，模型加载约 1–3 分钟）
./PicoSeek status         # 等到 vLLM 和 LiteLLM 都列出模型名
./PicoSeek ask "SGLT2抑制剂对HFpEF患者有什么获益？"
```

约 2–4 分钟后终端打印答案，并保存到 `answers/<时间戳>.md`。答案结构：

- **结论** —— 直接回答问题
- **证据** —— 每条末尾带 `[n]` 引用，注明研究类型 / 样本量 / 效应量
- **局限** —— 证据质量、矛盾之处
- **参考文献** —— 每条标注 `〔全文(PMC)〕 / 〔全文(PDF)〕 / 〔全文(机构订阅)〕 / 〔仅摘要〕`

`answers/<时间戳>_papers/` 里能看到每篇文献的原文和模型阅读笔记，可以现场打开给观众看"答案是从哪来的"。

常用参数：
```bash
./PicoSeek ask --papers 12 "..."      # 多读几篇（默认 8 篇）
./PicoSeek ask --no-paywall "..."     # 不使用机构订阅下载
./PicoSeek ask --no-kb "..."          # 不做原子知识抽取 / 向量入库（更快）
./PicoSeek stop                       # 演示结束后停止服务
```

### 1.1 文献筛选（类 Google Scholar）

```bash
./PicoSeek ask --years 3 "..."                       # 近三年
./PicoSeek ask --year 2018-2023 "..."                # 自定义年份区间（或单年 --year 2022）
./PicoSeek ask --quartile Q1,Q2 "..."                # 只要一区/二区期刊（也可写 --zone 1-3、"一区,二区"）
./PicoSeek ask --journal "Nature,Lancet,JAMA" "..."  # 期刊名包含关键字（Nature 子刊 = Nature Medicine 等都会命中）
./PicoSeek ask --years 3 --quartile 1-2 --journal Nature "..."   # 可组合
./PicoSeek ask --quartile Q1 --keep-unranked "..."   # 分区表里查不到的期刊也保留（默认丢弃）
```

- 分区数据来自 `data/journal_ranks/scimagojr_<年>.csv`（SCImago SJR Best Quartile，Q1–Q4 ≈ 一区–四区），
  按 ISSN 匹配、刊名兜底。更新：`./PicoSeek rank download 2025`；查询：`./PicoSeek rank lookup "Lancet"`。
- 要用**中科院分区**：把分区表导出的 CSV 放到 `data/journal_ranks/cas_2025.csv`（需含 ISSN 或刊名列 + 含“分区”的列，
  Top 列可选），会自动加载并覆盖 SCImago 的结果。
- 分区表按文件签名（文件名 / mtime / 大小）热重载：换表或新表落盘后，长驻进程（API、worker）在下一次 `journal_rank.load()` /
  `lookup()` 时自动感知，不用重启。`./PicoSeek rank stats` 打印当前加载了哪几张表与索引规模。
- 筛选时会自动扩大候选池（每条 query 取 30+25 条）再过滤，日志里能看到各条件淘汰了多少篇。
- 答案开头与每条参考文献都标注分区，如 `〔Q1 SJR 6.90〕`。

### 1.2 引文定位到原文段落

- 全文/摘要被切成带编号的段落（`¶1, ¶2, …`，PMC 全文按 XML 的 `<p>` 切；PDF 按页/空行切；摘要按 BACKGROUND/METHODS 切），
  `answers/<ts>_papers/<pmid>.md` 里每段都有锚点 `<a id="p12">`。
- 模型阅读时每条发现必须写成 `(¶12: "原文逐字引用")`；程序用模糊匹配核对引用是否真的出现在该段（对不上会在全篇里重新定位，
  找不到则标为未核实），结果存 `<pmid>_citations.json`。
- 综合答案里的引用变成 `[3¶12]` → 可点击链接 `<ts>_papers/<pmid>.md#p12`；文末 **原文定位** 一节按篇列出每个被引段落的
  章节/页码和核实过的原文摘句。
- 模型偶尔会把标记写成组合或区间（`[3¶26, 3¶29]`、`[3¶1-¶5, 3¶3]`），归一化会逐个还原成单标记：区间按段落展开并核对
  段落是否真实存在，跨度超过 8 段视为泛指全篇、未知段落一律降级为 `[3]`；括号里夹了正文（`[3¶1 见表]`）则整组按原文保留。

### 1.3 原文保存 + 原子知识 + 向量知识库

每篇读过的文献都会持久保存到 `library/<pmid>/`：`fulltext.md`（带锚点全文）、`paragraphs.json`、`facts.json`（原子知识）、`meta.json`。
原子知识 = 模型从全文抽出的一句话事实（含人群/干预/对照/结局/数字，中英双语），每条带来源段落 `pid` 和逐字 `quote`，并经过同样的核实。
所有事实和段落向量化后追加到 `kb/`（bge-m3 embedding，模型放在 `models/BAAI/bge-m3`；没有模型时退化为哈希词袋向量）。

索引保存为单个 `kb/index.npz` 快照，旧三文件索引会在下次入库时迁移。CLI 与 HTTP worker 共用 `kb.lock`，串行执行增量写入和重建；重建期间检索仍可读取完整的旧代。共享目录与文件锁的部署要求见[架构说明](../docs/architecture.md#知识库与-cli-共存)。

本机 `PicoSeek ask` 保持同步抽取与入库。通过 API 执行的 `ask` 则先交付答案，另建持久的 `answer_kb` 任务后台入库，不让事实抽取与嵌入计算阻塞答案阅读；关闭 `use_kb` 时不会创建该任务。后台依赖逐篇原文快照、事实检查点与数据库游标恢复，不会在重试时重写已经交付的答案，详见[架构说明](../docs/architecture.md#答案先交付知识后台入库)。

```bash
./PicoSeek kb search "SGLT2 HFpEF 心衰住院"     # 语义检索（中英文均可）
./PicoSeek kb search "..." --kind paragraph      # 只搜原文段落
./PicoSeek kb stats / reindex                    # 统计 / 从 library/ 重建索引
./PicoSeek ask --kb-hits 5 "..."                 # 答案末尾附上知识库中相关的旧事实
```
Codex 模式下对应工具：`kb_search`。

## 2. 命令一览（`./PicoSeek help`）

| 命令 | 作用 |
|---|---|
| `./PicoSeek start [14b\|4b] [GPU]` | 启动 vLLM + LiteLLM，默认 Qwen3-14B 在 GPU 2；显存紧张用 `./PicoSeek start 4b 1` |
| `./PicoSeek stop` / `status` / `logs` | 停止 / 查看状态 / 打开 tmux 看服务日志 |
| `./PicoSeek ask "问题"` | **一键流水线**（推荐演示用，每次都真实检索 + 读全文，结果确定） |
| `./PicoSeek codex ["问题"]` | Codex 交互式 agent：模型自己决定调哪些工具，适合追问（"把 [2] 的摘要贴出来"、"读一下 [3] 的 Results"） |
| `./PicoSeek paywall get DOI` | 用机构订阅登录态下载付费墙全文到 `pdfs/` |
| `./PicoSeek verify` | 核对最近一次 Codex 回答里的 PMID/DOI 是否都来自工具返回（防编造） |
| `./PicoSeek test` | 不经过模型，直接测试检索工具是否可用 |

## 3. 目录结构

```
core/
├── PicoSeek                 统一入口脚本（上表所有命令）
├── scripts/                 由 PicoSeek start 调用的本机模型启动脚本
│   ├── vllm.sh                vLLM 服务（:8000）
│   └── litellm.sh             LiteLLM 代理（:4000）
├── litellm_config.yaml      LiteLLM 配置
├── picos_paths.py           数据目录约定（PICOSEEK_DATA 及其子目录）
├── ask.py                   一键流水线主程序（筛选 / 段落定位 / 知识抽取）
├── ingest.py                单篇入库（上传 PDF 或按 DOI 经机构访问下载 → 分段 → 抽事实 → library + kb）
├── interpret.py             文献解读（一组上传 PDF → 解析 → 综述 / 问答；HTTP 专用，无 CLI 入口）
├── literature.py            上游文献检索（Semantic Scholar / PubMed / Europe PMC）
├── journal_rank.py          期刊分区表加载与查询（SCImago / 自定义中科院分区 CSV）
├── knowledge_store.py       段落切分、引文核实、原子知识抽取、向量知识库
├── semantic_scholar_mcp.py  Codex 用的 MCP 工具服务（检索 / 全文 / 本地 PDF / kb_search）
├── paywall_fetch.py         机构订阅全文下载（Playwright）
├── spider_auth.py           拦截页 / 登录态检测（来自 SPIDER_PROJECT）
├── verify_citations.py      引用核对
├── test_mcp.py              不经模型的检索工具自测（./PicoSeek test）
├── AGENTS.md                Codex 的系统指令（检索工作流 + 回答格式），CLI 与 codex 引擎共用
├── tests/                   pytest 用例
└── data/journal_ranks/      期刊分区表（scimagojr_2024.csv 入库）
```

本地未设置 `PICOSEEK_DATA` 时，运行期数据也落在 `core/` 下且均不入库：`answers/`、`library/`（`kb reindex` 的数据源）、`kb/`（`index.npz`，换代靠一次 rename）、`docsets/`、`models/`（见 §6.1）、`pdfs/`、`var/`、`logs/`。各目录内容见 [architecture.md § 数据目录](../docs/architecture.md#数据目录)。

## 4. 怎么证明答案不是编的

1. `answers/<ts>_papers/` 里每篇都有原文和模型笔记，引用可逐条溯源。
2. Codex 模式下，运行输出里必须有 `mcp: semantic_scholar/xxx (completed)`；之后跑 `./PicoSeek verify`，不在工具返回里的 PMID/DOI 会标 `SUSPECT`。
3. 追问核对具体数字："把 [2] 的摘要原文贴出来" 或 "用 pubmed_fetch 36041474"。
4. 完整原始记录（含工具返回全文）：`~/.codex/sessions/<date>/rollout-*.jsonl`。

## 5. 全文获取

| 来源 | 说明 |
|---|---|
| PMC（开放获取） | 自动。Codex 里可说 "读一下 [2] 的全文 Results 部分"（`get_fulltext` 按章节读） |
| Unpaywall PDF | 自动 |
| 机构订阅 | 需先在**有显示器的机器**上登录一次生成 `sd_state.json`，见下 |
| 本地 PDF | 放到 `pdfs/`，Codex 里说 "用 read_pdf 读 xxx.pdf" |

`ask` 流水线的自动级联只有前三级：PMC XML → Unpaywall PDF → 机构订阅 PDF（需 `sd_state.json`），都拿不到就退回摘要。**`pdfs/` 下的本地 PDF 不参与该级联**，只是 Codex/MCP `read_pdf` 的输入。机构订阅那级需要 playwright 与 Chromium：runtime 镜像已安装；本机需在仓库根执行一次 `uv run playwright install chromium`。

机构订阅登录（一次性）：
```bash
# 在有浏览器的机器上：
./PicoSeek paywall login --url https://www.sciencedirect.com/
# 浏览器弹出 → 机构登录（OpenAthens / Shibboleth / CARSI）→ 打开一篇付费文章确认能看全文 → 回终端按 Enter
# 把生成的 sd_state.json 及两份伴随文件拷到服务器的 $PICOSEEK_DATA/var/（Compose 部署改走 Web 的 /admin/institution 上传）
# 服务器上验证：
./PicoSeek paywall get 10.1016/j.jacc.2023.10.021
```
之后 `./PicoSeek ask` 自动启用（每次最多下载 `PAYWALL_MAX_PER_RUN`=5 篇，串行、间隔 4–9 秒）。
注意：登录态可能与出口 IP 绑定；请只按需下载，遵守出版社许可。

## 6. 环境与配置

- Python 环境：默认仓库根 uv workspace 的 `.venv`（仓库根执行 `uv sync --all-packages`，pypdf / playwright / mcp / torch / sentence-transformers 均由它安装）；用 `PICOSEEK_ENV` 可指向任意 conda/venv 目录（`PicoSeek` 与 `scripts/*.sh` 共用此变量，缺失时直接报错退出）
- Qwen3 权重：默认 `<项目根>/models/Qwen/Qwen3-14B`、`.../Qwen3-4B`，可用 `QWEN3_14B` / `QWEN3_4B` 覆盖；`vllm`、`litellm` 需装在 `PICOSEEK_ENV` 指向的环境里（vLLM 需 NVIDIA GPU）
- Codex CLI 在 `~/.local/bin/codex`
- `LOCAL_QWEN_KEY` 需等于 `litellm_config.yaml` 的 `master_key`（`PicoSeek` 已默认设置）
- 可选 API key：`S2_API_KEY`（Semantic Scholar，无 key 时基本 429，会自动退到 PubMed）、`NCBI_API_KEY`（PubMed 3→10 req/s）
- Codex 配置 `~/.codex/config.toml` 要点：
  - `model = "qwen3-14b"`, `model_provider = "local-qwen"`, `base_url = http://127.0.0.1:4000/v1`, `wire_api = "responses"`
  - `[mcp_servers.semantic_scholar] default_tools_approval_mode = "approve"` —— 否则 `codex exec` 下 MCP 调用会被拒，模型会凭记忆编参考文献

### 6.1 embedding 环境（`kb` 子命令，无 GPU 亦可）

`models/`（约 2.2G 权重）不入库。torch（CPU wheel）与 sentence-transformers 已由仓库根 `uv sync --all-packages` 装进 workspace `.venv`（`core[embed]`，Python 3.12）；只需下载模型：

```bash
cd core
uv run python -c "from huggingface_hub import snapshot_download as d; \
  d('BAAI/bge-m3', local_dir='models/BAAI/bge-m3', \
    ignore_patterns=['onnx/*','*.onnx','*.onnx_data'])"
```

- 目标路径固定为 `<PICOSEEK_DATA>/models/BAAI/bge-m3`（本地即 `core/models/BAAI/bge-m3`；`knowledge_store.py` 的 `EMBED_MODEL` 默认值，可用同名环境变量覆盖）。
- 末级目录名必须正好是 `bge-m3`：`Embedder` 的后端名取自 `os.path.basename(model_path)`，要与索引里记的 `"embedder": "bge-m3"` 一致。
- 排除 `onnx/`：上游的 `onnx/model.onnx_data` 单独 2.27G，与 `pytorch_model.bin` 是同一模型的另一份格式，默认的 torch 后端只读 `.bin`。排除后约 2.2G。想用 ONNX 后端不需要下它，按 §6.3 自己导出 int8 权重（约 570M）即可。
- 无 GPU 无需改代码，`Embedder` 只在 `torch.cuda.is_available()` 为真时才切 GPU；CPU 路径的调优见 §6.2。
- **装完必须验证**：缺模型或缺 torch 时 `Embedder` 只打一行 log 就静默退化成 `hash-bow-v1`(4096 维)。它与索引里记的 `bge-m3` 不是同一后端，`search` / `add_paper` 会抛 `EmbedderMismatch` 并说明该重建还是该修环境——但**报错时机是第一次读写**，所以别等到那会儿才发现。

```bash
cd core
uv run python -c "from knowledge_store import Embedder; e=Embedder(); print(e.name, e.dim)"
# 必须打印: bge-m3 1024   （打印 hash-bow-v1 4096 就是没加载上）
./PicoSeek kb stats
./PicoSeek kb search "SGLT2 HFpEF 心衰住院"
```

> **本节及下面两节的命令都不会读 `.env`。** `knowledge_store.py` 只认 `os.environ`，
> 没有 dotenv 加载；所以直接 `python knowledge_store.py ...` 拿到的是各变量的默认值
> （实测 `EMBED_THREADS=0` / `EMBED_BACKEND=torch`），跟 API、worker 的实际配置可能
> 不一致。要复现服务侧的行为，请按 §6.3 的 b) 显式 `set -a && . ../.env && set +a`。

### 6.2 编码吞吐标定（`EMBED_THREADS` / `EMBED_BATCH_TOKENS`）

CPU 上入库的绝大部分时间花在 bge-m3 的前向推理上，而它对**线程数**极其敏感，最优值
又只能按机器实测——容器和虚拟机读不到宿主的 SMT 拓扑（本机 KVM guest 报 24 个 vCPU、
`Thread(s) per core: 1`，宿主其实是 16 核 32 线程），所以代码不做任何推断，只认
`EMBED_THREADS`。**留空就沿用 torch 默认（= 逻辑核数），实测往往正好是最差的一档。**

```bash
cd core
for t in 6 8 12 16 24; do EMBED_THREADS=$t python - <<'PY' 2>&1 | tail -1
import json, os, time
import knowledge_store as ks
texts = []
for d in sorted(os.listdir(ks.LIB_DIR)):
    p = os.path.join(ks.LIB_DIR, d, "paragraphs.json")
    if os.path.exists(p):
        texts += [x["text"] for x in json.load(open(p))]
    if len(texts) >= 128:
        break
texts = texts[:128]
e = ks.Embedder()
e.encode(texts[:4])                      # 预热，别把首批的一次性开销算进去
t0 = time.perf_counter(); e.encode(texts); dt = time.perf_counter() - t0
print(f"EMBED_THREADS={os.environ['EMBED_THREADS']:>3}: {dt/len(texts)*1000:6.1f} ms/条")
PY
done
```

把最快的那档写进 `.env`。24 vCPU / 宿主 7950X3D 上的实测曲线（128 段真实文本、23.7k token）：

| threads | 6 | 8 | 10 | **12** | 14 | 16 | 20 | 24 |
|---|---|---|---|---|---|---|---|---|
| ms/条 | 242 | 191 | 183 | **156** | 167 | 173 | 182 | 420 |

`EMBED_BATCH_TOKENS`（默认 1024）是单批 **padding 之后** 的 token 上限。批的真实成本是
`最长那条 × 批内条数`，所以固定条数的分批遇到长短混排会大量空转：同一批文本用
`batch_size=16` 要 436 ms/条、`batch_size=128` 要 1893 ms/条。注意两个参数有先后——
线程数没调对时按 token 分批几乎白干（1.04×），调对之后才拿到 2.24×。

### 6.3 可选：ONNX int8 后端（`EMBED_BACKEND=onnx`）

比 torch fp32 快约 2.2×（156 → 76 ms/条），代价是**向量会变**：实测与 fp32 的 `cos` 最低
0.937、top-5 邻居重合率只有 0.875，检索结果会漂。所以两个后端的向量**不能共存于一份
索引，也不能跨进程混用**——`Embedder.name` 带上 `+onnx-int8` 后缀，`search` 与 `add_paper`
在读到非空且标签不符的索引时直接抛 `EmbedderMismatch`，不是打一行警告就继续。
注意维度检查在这里帮不上忙：两者都是 1024 维。

`optimum` 故意**不在** `pyproject.toml` 里：uv 的 lock 对所有 extra 统一求解，即便没人装
这个 extra，它也会把 transformers 压到 `<5`、连带把 sentence-transformers 从 6.0.1 拖回
5.7.0。所以它只能手动装，并且要清楚这会降级上面两个包：

```bash
uv pip install "optimum[onnxruntime]>=1.24"      # 会降级 transformers / sentence-transformers
cd core && python -c "
from sentence_transformers import SentenceTransformer
from sentence_transformers.backend import export_dynamic_quantized_onnx_model
import knowledge_store as ks
m = SentenceTransformer(ks.EMBED_MODEL, backend='onnx')      # 首次会先导出 fp32 ONNX，约 21s
export_dynamic_quantized_onnx_model(m, 'avx512_vnni', ks.EMBED_MODEL)
"
# 产出 models/BAAI/bge-m3/onnx/model_qint8_avx512_vnni.onnx（约 570M）
```

接着**把 `EMBED_BACKEND=onnx` 写进 `.env`**，让 API 与 worker 都持久用同一个后端
（`compose.yaml:4` 的 `env_file` 会同时喂给两者），重启整栈，最后重建整库——两条路选一条：

```bash
# a) 跑着 compose 栈：交给已经按 env_file 配好环境的 worker（需管理员，返回 202 + job）
curl -X POST -H "Authorization: Bearer <管理员 token>" \
  "http://127.0.0.1:${PICOSEEK_PORT:-8765}/v1/kb/reindex"
# 是异步任务，进度看 GET /v1/jobs/<返回的 id>；3063 条 / 64 篇实测约 7.6 分钟

# b) 本机直接跑 core/：得自己把 .env 带进这条命令。knowledge_store.py 只读
#    os.environ、不会加载 .env——直接 `python knowledge_store.py reindex` 实测拿到的是
#    EMBED_THREADS=0 / EMBED_BACKEND=torch，等于用默认后端重建，白跑一趟。
cd core && set -a && . ../.env && set +a && python knowledge_store.py reindex
```

只让重建这一步用上 onnx 是**不够的**：那样索引是 int8 的，而 API 进程仍用 fp32 编码
query，每一次 `/v1/kb/search` 都会撞 `EmbedderMismatch`（在加上这道拦截之前，它会静默
返回漂掉的结果）。回退到 torch 同理——改回 `.env` 之后必须再重建一次。


## 7. 排错

| 现象 | 处理 |
|---|---|
| `./PicoSeek status` 显示 not ready | 等 1–3 分钟；`./PicoSeek logs` 看 vLLM 是否还在加载 / 显存不足 |
| 显存不够 | `./PicoSeek stop && ./PicoSeek start 4b <空闲GPU>` |
| 检索无结果 / 429 | `./PicoSeek test` 直接测工具；配置 `S2_API_KEY` |
| Codex 回答没有 `mcp:` 行 | 检查 `~/.codex/config.toml` 的 `default_tools_approval_mode = "approve"` |
| 手动验证代理 | `curl -H "Authorization: Bearer sk-123456" http://127.0.0.1:4000/v1/models` 应列出 `qwen3-14b` |

*本工具输出为文献综述，仅供科研/教学参考，不构成医疗建议。*
