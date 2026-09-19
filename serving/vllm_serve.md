# 服务层部署：在自托管 GPU 上跑通模型推理

> 目标：把 mini-tbench 从「调外部 agent CLI」升级为「自托管 coding 模型 + 自写 agent 循环 +
> 并发批量 rollout」。这一层直接证明你能解决 JD 里隐性的**并发、长上下文、稳定性**问题。

## 部署到自托管 GPU（本地卡 / 云卡均可）

> 说明：`harness/` 主体（任务、沙箱、verifier、rollout、reward、去重、抽检、污染与作弊分析）
> **完全不需要 GPU**，纯 CPU 即可跑通（本项目就是在无显卡环境验证的）。
> 需要 GPU 的只有下面这一层 —— 自托管模型来批量生成 agent 轨迹。

```bash
# —— 方案 A：vLLM（基线，吞吐稳健，OpenAI 兼容）——
pip install vllm==0.6.3
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-Coder-7B-Instruct \
  --host 0.0.0.0 --port 8000 \
  --max-model-len 32768 \          # 长上下文：覆盖多轮工具调用轨迹
  --enable-prefix-caching \         # 长上下文：system+task 前缀命中缓存，降延迟
  --max-num-seqs 256 \              # 并发：单卡可排队并发的请求数（压测调优对象）
  --gpu-memory-utilization 0.90 \   # 稳定性：留余量防 OOM
  --enable-chunked-prefill          # 长上下文：大 prompt 分块预填充，避免长输入卡死
  --served-model-name coder

# —— 方案 B：SGLang（推荐用于多共享前缀的 rollout）——
pip install sglang
python -m sglang.launch_server \
  --model-path Qwen/Qwen2.5-Coder-7B-Instruct \
  --host 0.0.0.0 --port 8001 \
  --context-length 32768 \
  --mem-fraction-static 0.85 \
  --enable-radix-attention          # 核心：RadixAttention 让 N 条 rollout 共享前缀 KV，吞吐翻倍
```

## 每个 flag 对应岗位什么能力（面试可逐条讲）

| 启动参数 | 解决的真实问题 | 在报告里怎么量化 |
|---|---|---|
| `--max-num-seqs` | 并发：单卡能同时跑多少 rollout | 并发 64/128/256 下的 p50/p95 延迟、token 吞吐 |
| `--max-model-len` + chunked-prefill | 长上下文：轨迹越来越长不崩 | 轨迹 token 数分布、超长轨迹触发摘要的比例 |
| `--enable-prefix-caching` / radix | 长上下文 + 成本：前缀命中率 | prefix cache hit rate（SGLang 看 `/metrics`） |
| `--gpu-memory-utilization` | 稳定性：防 OOM | 压测期间 OOM 次数 |
| `--served-model-name` | 多模型：A/B 对比训练数据质量 | 同一任务对 Qwen/DeepSeek 的成功率差异 |

## GPU 规格建议：跑得动 vs 跑得快

显存需求 = **权重 + KV cache + 激活开销**。权重是固定项，KV cache 才是长上下文 rollout 的真正变量。

| 目标 | 模型 | 量化 | 权重 | KV（32k × 16 并发） | 建议卡 |
|---|---|---|---|---|---|
| 只跑通链路 | Qwen2.5-Coder-1.5B | BF16 | ~3 GB | ~4 GB | 任意 8–16 GB 卡（T4 / 2080Ti） |
| 低成本出轨迹 | 7B | AWQ 4bit | ~5 GB | ~7 GB | 1× 24 GB（4090 / A5000），ctx 降到 8–16k |
| 标准基线（推荐起步） | 7B | BF16 | ~14 GB | ~29 GB | 1× 48 GB 起（A6000 / L40S） |
| 长上下文 + 高并发 | 7B | BF16 | ~14 GB | ~29 GB | 1× 80 GB（A100 / H100）或 2× 24 GB TP=2 |
| 更强代码能力 | 14B / 32B-AWQ | BF16 / 4bit | ~28 / ~18 GB | ~29 GB | 1× 80 GB |
| 接近生产规模 | 32B | BF16 | ~64 GB | ~58 GB | 2× 80 GB（TP=2） |
| 生产规模 | 70B / 32B-FP8 | BF16 / FP8 | ~140 / ~32 GB | 视并发 | 4–8× 80 GB（TP/EP） |

### KV cache 怎么算（面试会被追问，背这个算式）

以 Qwen2.5-Coder-7B 为例：28 层、4 个 KV head、head_dim 128、FP16：

```
每 token 每序列 = 2(K+V) × 28 层 × 4 head × 128 dim × 2 Byte ≈ 56 KB
32k 上下文单条轨迹       = 32768 × 56 KB            ≈ 1.8 GB
32k × 16 并发            = 1.8 GB × 16              ≈ 29 GB
```

**结论：并发和上下文是两个乘数，不是加法。** 这也是为什么"长上下文 + 高并发"必须上 80 GB 卡 ——
14 GB 权重看着很轻，但 KV 一乘就压不住。压缩手段：GQA（选 KV head 少的模型）、
FP8 KV cache、`--max-model-len` 按实测轨迹长度裁剪、超长轨迹先摘要再回灌。

### 到底要多少卡才"快"

吞吐瓶颈**不是 decode，而是 prefill** —— coding agent 每条轨迹有 10–30 轮工具调用，
每轮都要把全部历史重新喂进去，序列还越来越长。所以要看的指标是**聚合 token 吞吐**和
**前缀命中率**，不是单请求速度。

- **前缀命中率是第一杠杆**：大量 rollout 共享同一套 system prompt + 任务模板，
  SGLang RadixAttention / vLLM prefix caching 能把这部分前填充整段跳过。
  命中率从 0 提到 60%+，等效吞吐提升远超加卡。
- **量级参考（须实测回填，不预填估算值）**：8× 80 GB 跑 7B–14B，
  按单条轨迹约 60k token 计，每小时可产千条量级轨迹。真实数字由
  `rollout_manager.py` 阶梯压测得出后写进失败归因报告。
- **性价比拐点**：先单卡把 `--max-num-seqs` 调到吞吐拐点（再大只涨延迟不涨吞吐），
  再决定加卡还是降模型。加卡之前先把前缀缓存和上下文裁剪做满。

### 如果要训练（SFT / RL），那是另一套资源

本项目只**产出**训练数据，不训练模型。真要在同一套数据上训，量级完全不同：

| 训练方式 | 模型 | 最低配置 | 备注 |
|---|---|---|---|
| QLoRA SFT | 7B | 1× 24 GB | 4bit + LoRA，bs=1、2k ctx，能跑但慢 |
| LoRA SFT | 7B | 1× 48–80 GB | BF16 全层 LoRA，实用起点 |
| 全参 SFT | 7B | 8× 80 GB | ZeRO-3 / FSDP |
| RL（GRPO / PPO） | 7B | 8× 80 GB 起 | **还要额外留 rollout 推理算力**，可与训练共卡但要算清显存 |
| RL | 32B | 32–64× 80 GB | 工业级数据生产规模 |

> 面试话术差异很重要：被问"你这项目要多少 GPU"，正确回答是
> 「**产数据不吃训练卡**——harness 层纯 CPU，只有自托管 rollout 需要推理卡；
> 一张 48 GB 能起 7B 基线并发压测，8× 80 GB 才叫数据生产规模。
> 真正的训练算力在客户侧。我关心的是**前缀命中率、上下文裁剪、并发拐点**这三个乘数。」

## 容量压测（有卡后做，进报告）

```bash
# 并发 1 → 64 → 128 → 256 阶梯加压，记录吞吐拐点
python serving/rollout_manager.py --model coder --concurrency 64 --runs 30
python serving/rollout_manager.py --model coder --concurrency 256 --runs 30
```

> 拐点数据本身就是「数据生产工程能力」的最强证据——大多数只会写 agent 的人讲不出这个。

---

## 私有化 / 内网独立部署（完全离线，可交付到客户内网）

> **vLLM / SGLang 本身就是私有化部署引擎**：开源推理框架，跑在自有内网 GPU 上，
> 对外只暴露 OpenAI 兼容端点，**数据、权重、推理全过程不出内网，无需任何公网出口**。
> 这一节给出"交付到禁外网环境"的具体做法——正是政府 / 国企类项目（如高速公路安全平台）的强制要求。

### 三件必须解决的工程事

| 事项 | 问题 | 做法 |
|---|---|---|
| **权重离线加载** | 不能在线从 HuggingFace 拉权重 | 权重预先下载为 `*.safetensors` + `config.json` 打包进镜像 / 挂载内网 NFS，启动指向本地路径：`--model /opt/models/Qwen2.5-Coder-7B-Instruct` |
| **内网依赖源** | 不能 `pip install` 走公网 | ① 内网私有 PyPI 镜像（`pip config set global.index-url http://<内网镜像>/simple`）；② 或直接预置 wheel 离线安装：`pip install --no-index --find-links=/opt/wheels vllm` |
| **信创 OS 适配** | 交付环境为 RHEL7 / 国产 Linux | 基础镜像用 `anolis`/`openEuler`/`ubi` 系；CUDA/cuDNN 版本与驱动对齐；glibc 版本校验 |

### 离线启动（禁外网环境）

```bash
# 关键：切断一切公网回连，强制走本地
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_HOME=/opt/models            # 权重与缓存全部本地化
export VLLM_NO_USAGE_STATS=1          # 关闭遥测上报
export HTTP_PROXY= HTTPS_PROXY=       # 清空代理，确保无出口

# vLLM 指向本地权重路径（而非 HF repo id）
python -m vllm.entrypoints.openai.api_server \
  --model /opt/models/Qwen2.5-Coder-7B-Instruct \
  --host 0.0.0.0 --port 8000 \
  --max-model-len 32768 \
  --enable-prefix-caching \
  --max-num-seqs 256 \
  --gpu-memory-utilization 0.90 \
  --enable-chunked-prefill \
  --served-model-name coder

# SGLang 同理
python -m sglang.launch_server \
  --model-path /opt/models/Qwen2.5-Coder-7B-Instruct \
  --host 0.0.0.0 --port 8001 \
  --context-length 32768 \
  --mem-fraction-static 0.85 \
  --enable-radix-attention
```

### 离线镜像交付

```dockerfile
# 预置权重 + 预置 wheel，构建一次即可在内网反复部署
FROM nvcr.io/nvidia/cuda:12.1-runtime-ubi8     # 或 anolis / openEuler 基础镜像
COPY wheels/ /opt/wheels/
RUN pip install --no-index --find-links=/opt/wheels vllm==0.6.3
COPY models/Qwen2.5-Coder-7B-Instruct /opt/models/Qwen2.5-Coder-7B-Instruct
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HOME=/opt/models
EXPOSE 8000
```

### 验收检查清单（交付前自检）

- [ ] 断网启动成功（拔网线 / 清空代理后 `curl localhost:8000/v1/models` 正常返回）
- [ ] 无任何外网 DNS 解析（抓包或 `ss -tnp` 确认无外部连接）
- [ ] 权重路径为本地绝对路径，无 HF repo id
- [ ] p95 延迟与 OOM 指标在目标并发下达标
- [ ] 镜像可在干净内网机器 `docker load` 后直接跑通

> 面试话术：「vLLM/SGLang 我做过完全离线的私有化部署——权重本地化、依赖走内网镜像、
> 断网可启动、无任何外网回连。这不是调 API，是把推理栈真正交付进客户内网。」
