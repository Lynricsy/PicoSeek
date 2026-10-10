# 远端模型服务记录

生产栈依赖的 Qwen3.8-27B 推理服务不在本仓库内，部署在独立 GPU 主机上，经 SSH 隧道接入（接入方式见 [deployment.md § 当前生产](deployment.md#当前生产经-ssh-隧道接远端-vllm)）。本页记录它的现状、运维操作和历次变更，供接手者维护或迁移。数值均为当时实测，不是规格承诺。

## 现状（2026-09-19 起）

| 项 | 值 |
|---|---|
| 主机 | A6000 主机 `tx-06`（ZeroTier `10.107.231.181`） |
| GPU | GPU3，RTX A6000 49140MiB，`GPU-7d090f4e-ef66-e2e0-4774-1d5daa48d1cd`；同机 GPU0/1/2 被三个 ollama 容器按 `DeviceIDs` 钉死 |
| 目录 | `/data1/lings/Qwen3_8_27B`（`serve.py`、`service.py`、`gpu_guard.py`、`deployment.json`） |
| 服务 | systemd 用户 unit `lings-qwen38-27b-29913.service`，`Restart=always`、`RestartSec=30`，`ExecStartPre` 跑 `gpu_guard.py` |
| 监听 | `127.0.0.1:29913`，服务模型名 `Qwen3.8-27B`，上下文 262144 |
| 推理引擎 | vLLM `0.29.0+cu129`（GitHub release 制品），`--reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_xml` |
| 权重 | NVFP4 `RedHatAI/Qwen3.8-27B-NVFP4`，位于 `models/Qwen3.8-27B-NVFP4` |
| 投机解码 | DSpark 七步：`{"method":"dspark","model":"…/draft","num_speculative_tokens":7}`，草稿模型 `RadixArk/Qwen3.8-27B-DSpark` 位于 `performance/nvfp4-dspark-20260919/draft` |
| 关键参数 | `kv_cache_dtype: "fp8"`、`linear_backend: "marlin"`（必需）、FlashInfer、前缀缓存、`max_num_seqs=4`、`gpu_memory_utilization=0.92` |
| KV 容量 | 300,434 tokens（单请求 262144 上下文的 1.15 倍）；四路只是调度上限，不代表能同时跑四份 256K 请求 |

参数集中在 `deployment.json`，由 `serve.py` 读取；它们不是本项目 Compose 的参数。应用侧的思考策略、超时与 `llm_metrics` 日志见 [architecture.md § 模型调用策略](architecture.md#模型调用策略)。

### 两条必须知道的约束

- **`--linear-backend marlin` 是必需项**：auto 内核下 compressed-tensors 会给 NVFP4 权重选 W8A16 FP8 scheme，启动崩在 `humming_utils.py:489` 的 `AttributeError: 'ParallelLMHead' object has no attribute 'output_partition_sizes'`。
- **KV 只能是 fp8**：FlashInfer 的 nvfp4 KV 只放行 Blackwell（`is_device_capability_family(100)`），A6000 为 sm_86。A6000 没有原生 FP8，但 FP8 KV 在 FlashInfer 路径上可用；vLLM 里「FP8 KV 需 SM89+」的门槛只在 Triton 路径。

## 运维操作

- **重启**：`/data1/lings/Qwen3_8_27B/.venv/bin/python /data1/lings/Qwen3_8_27B/service.py restart`，然后等 `/health` 成功，不能只看 systemd 的 `active`。
- **占卡保护**：`gpu_guard.py` 按 `deployment.json` 的 `gpu_uuid` 与 `gpu_memory_utilization` 计算所需空闲显存，不足则以退出码 1 拒绝启动并写 `logs/server.log`，交给 `RestartSec` 重试，不与他人抢卡。手工核查：`gpu_guard.py --uuid <UUID>` 或 `--min-free-mib <N>`。
- **回滚到 FP8 + MTP**：先暂停请求生产者（停 worker）并确认无进行中请求；把 `serve.py.fp8-mtp.bak` 与 `deployment.json.fp8-mtp.bak` **成对**恢复为原文件，`systemctl --user restart lings-qwen38-27b-29913.service`，等 `/health` 成功后恢复 worker。同目录 `rollback_guard.sh` 会在健康检查失败时自动做这件事。
- **重新拉权重**：`tools/fetch_weights.py` 按 `model-manifest.json` 从 ModelScope 下载并逐文件 SHA256 校验，可重复运行。
- 不要给生产转发密钥扩大 shell 权限；共享 GPU 的归属需要与同机项目约定，`gpu_guard.py` 不能替代约定。

## 隧道侧配置

- `.llm-ssh/config` 的 `qwen-target` 指向 `10.107.231.181`，经跳板 `qwen-jump` 连接。
- 跳板上该专用公钥的 `permitopen` 同时允许 `10.107.231.69:22`（旧主机 slave2）与 `10.107.231.181:22`；缺失时隧道报 `channel 0: open failed: administratively prohibited`。跳板 `authorized_keys` 备份为 `authorized_keys.pre-a6000-20260918`。
- tx-06 上以 `restrict,port-forwarding,command="/bin/false",permitopen="127.0.0.1:29913"` 登记同一公钥；首连指纹 `SHA256:dkS36ZUg3B/OjLveBBl4JWvLHMv7i5u517xIdeRw5Kc`（ssh-ed25519）已写入 `.llm-ssh/known_hosts`。

## 在 tx-06 复刻环境的坑

1. 解释器必须用 uv 装的 CPython 3.12.14（`UV_PYTHON_INSTALL_DIR=<项目>/runtime/python`）。系统 `python3.11` 是 Ubuntu 的 `3.11.0rc1`，缺 `sys.get_int_max_str_digits`，`import torch` 直接失败。
2. 不能用 PyPI 默认的 `vllm==0.29.0` / `torch==2.13.0`：它们依赖 `nvidia-*-cu13`，需要 r580+ 驱动，而该机驱动是 560.35.05（CUDA 12.6）。必须用 GitHub release 的 `vllm-0.29.0+cu129`（SHA256 `22e8d8fe…89fe`）配 `requirements.lock` 里的 `+cu129` 轮子。
3. 该机国际源只有 KB/s：普通包走清华 PyPI（约 10MB/s），torch 系列走上海交大或阿里云 `pytorch-wheels/cu129`，权重走 ModelScope（约 5MB/s），GitHub release 走 `gh.xxooo.cf` 镜像（约 1.7MB/s）后用 SHA 校验。
4. `/usr/bin/nvcc` 是 CUDA 11.5，FlashInfer JIT 用它编译会报 `nvcc fatal: Unknown option '--compress-mode=size'`。`serve.py` 因此设置 `CUDA_HOME=/usr/local/cuda` 并把 `/usr/local/cuda/bin` 放进 `PATH`（该机 `/usr/local/cuda -> cuda-12.6`），原文件备份为 `serve.py.pre-cuda-home-20260918`。

## 变更记录

### 2026-09-19：FP8 + MTP 单步 → NVFP4 + DSpark 七步

`serve.py` 改三处：模型路径改读 `config.get("model", …FP8)`、`--kv-cache-dtype` 改读 `config.get("kv_cache_dtype", "fp8")`、新增可选 `--linear-backend`。`deployment.json` 增加 `model`、`kv_cache_dtype`、`linear_backend` 并替换 `speculative_config`。原文件备份为 `serve.py.fp8-mtp.bak`、`deployment.json.fp8-mtp.bak`。

切换依据是生产真实 prompt 的对照评测：3 道真实问题 ×（2 篇 read + 1 次 synthesize）= 9 个任务，输入从 `core/answers/<id>_papers/` 存档逐字重建。

| 指标 | FP8 + MTP | NVFP4 + DSpark |
|---|---:|---:|
| read 阶段吞吐（tok/s） | 35.71 | 98.98 |
| synthesize 阶段吞吐（tok/s） | 37.91 | 101.43 |
| 引用原文核验率 | 0.979（复跑 0.956） | 0.943 |
| KV 容量（tokens） | 347,528 | 300,434 |

其余质量项：PICOS 六标题三轮均 6/6；综合阶段非法引用标记三轮均为 0；相关性判分 NVFP4 与存档生产 4/4 一致，FP8 复跑反而有 2 处不一致；A/B 正反序盲评忠实度 3/3 平局、均无编造数字、结论方向 3/3 一致。结论是「未观察到超出采样噪声的劣化」，不是「证明无劣化」（3 题 9 任务 3 轮）；如发现答案质量回退，按上文成对回滚。切换后端到端验收：真实问答 260 秒完成（切换前同类任务 297–610 秒），7 篇论文、58 个引用标记全部合法、16/16 条引文核实通过。

### 2026-09-18 晚：slave2（L20）→ tx-06（A6000）

同机另一项目再次取走 slave2 的 GPU2（journal 记录 `10:11:04 Succeeded.`，即进程被外部 `SIGTERM` 后以退出码 0 退出），`gpu_guard.py` 随后拒绝启动 474 次——保护按设计没有抢卡，但说明该机已无稳定容量，于是整体迁到 tx-06。目录结构与 slave2 一致（tx-06 的 `/data1/lings` 由 `sudo` 建出并授予 `tx`），`serve.py`、`service.py`、`gpu_guard.py`、unit 文件无需改路径。迁后 FP8 实测 `GPU KV cache size: 347,528 tokens`（比 L20 多约 30%），`MarlinFP8ScaledMMLinearKernel`、FlashInfer、`--kv-cache-dtype fp8`、MTP 单步全部生效；`marlin_utils_fp8` 的 no-native-FP8 告警与 L20 上一致。

slave2 上的 unit 已 `stop` 并移除 `default.target.wants` 软链（`UnitFileState=linked`），文件、权重与备份保留。该机两张 L20 已被对方占满，**回滚到 slave2 需要先协调出一张空卡**，只改回 `.llm-ssh/config` 不会生效。

### 2026-09-18：slave2 内换卡与自愈加固

因 GPU3 被同机另一项目训练占用，服务迁到同机另一张 L20（`gpu_uuid` 改为 `GPU-5166f61d-9213-e2b6-2077-93d850834675`），只改 `deployment.json` 这一个字段，备份 `deployment.json.pre-gpu2-20260918`。同时把 unit 的 `Restart=on-failure` 改为 `Restart=always`（`RestartSec=30`）并新增 `ExecStartPre` 的 `gpu_guard.py`：被外部 `SIGTERM`（退出码 0，`on-failure` 不触发）也能自愈。unit 原文件备份为 `lings-qwen38-27b-29913.service.pre-restart-always-20260918`。

### 2026-09-17：slave2（L20）上启用 MTP 并调度四路

vLLM `0.29.0+cu129`，保留 Marlin FP8 权重内核、FP8 KV、FlashInfer、前缀缓存与 262144 上下文；启用 `speculative_config={"method":"mtp","num_speculative_tokens":1}`，`max_num_seqs=4`，`gpu_memory_utilization=0.92`。KV 总容量约 266,637 tokens。独立重启后确认（相同输入，每项三轮取中位数；单流开启思考，四篇阅读关闭思考）：

| 热前缀固定输出负载 | 无 MTP、两路 | MTP 单步、四路 | 耗时减少 |
|---|---:|---:|---:|
| 单流综合，输入 14318、生成 1024 tokens | 42.58 秒 | 27.56 秒 | 35.3% |
| 四篇阅读闭合批次，每篇生成 512 tokens | 48.50 秒 | 20.49 秒 | 57.8% |

MTP 草稿接受率 94.39%，无 KV 抢占；八路上限没有超出波动的收益，Triton 线性后端更慢，均未采用；MTP 两步及三步在 0.92 显存比例下放不下完整 256K，未以缩短上下文换速度。固定输出额度只用于基准，不是生产生成限制，这些百分比也不能当作完整问答提速。功能验收输入 261192 tokens，正确找回开头校验码并自然结束；Responses 与自动工具调用通过。原始测量与指标保存在模型主机 `performance/20260917-mtp1-seq4/`，验收记录在 `verification.json` 的 `performance_tuning_20260917`。调优前文件备份为 `serve.py.pre-tune-20260917`、`deployment.json.pre-tune-20260917`。
