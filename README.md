# Mini-TBench：coding agent RL 数据任务实验台

> 一套**可运行、可验证、可复现**的 coding / agentic 任务集：任务构造 → 容器隔离执行 →
> 可执行 verifier + 反作弊 → 批量轨迹采集 → 奖励设计 → 数据质量体系 → 训练数据导出。
> 用于验证并证明 post-training 数据工程（SFT / RLVR 数据闭环）能力。

**Repo**: <https://github.com/Fanqingle/minitbench> · **License**: MIT

> 30 秒上手：`pip install pytest pyyaml && python scripts/demo_pipeline.py`
> 一条命令跑完「oracle 校验 → agent 采样 → 奖励计算 → 去重/抽检 → 作弊与污染检测」全链路。

---

## 一、JD 六项职责 → 本项目实现对照

| JD 职责 | 实现位置 | 可演示的证据 |
|---|---|---|
| ①任务构造、难度分层、轨迹采集、rubric/reward | `tasks/*/task.yaml`、`mini_tbench/task_spec.py`、`reward.py` | 3 个任务（medium/medium/hard），分层 rubric，RLVR scalar reward |
| ②拆解 coding agent 能力边界与失败模式 | `agent_runtime.py`、`step_trace.py`、`docs/agent-failure-mode-taxonomy.md` | 真实 LLM 驱动的 ReAct 循环 + **step 级失败归因**（11 类标签，可定位到「第几步 / 哪个工具」）；实测 30 条真实轨迹给出跨模型失败模式差异（premature_finish 占 27%） |
| ③可复现环境：容器隔离、超时与资源限制、批量 rollout | `sandbox.py`、`container.py`、`rollout.py` | Docker 断网 + 内存/CPU/pids 限制；常驻容器执行器（纯终端形态）；批量 rollout + 逐条落盘 |
| ④benchmark 分析、污染检测、verifier 被钻空子 | `redteam.py`、`verifier.py`、`harbor.py`、`contamination.py` | **13 类作弊 agent 红队 → 反作弊九项检查：12 拦截 / 0 漏网**；官方 Terminal-Bench / Harbor 任务接入 + 产物重建评分复现 |
| ⑤数据质量体系：校验、去重、人工抽检 | `mini_tbench/dedup.py`、`review.py`、`sft.py` | 精确+近似去重；抽检队列 CSV；Golden Dataset；SFT/RLVR 导出 |
| ⑥模糊目标 → 任务规范与验收标准 | `docs/client-task-spec-example.md` | 客户口语 → 可执行断言 + 轨迹标签 的完整翻译过程 |

---

## 二、目录结构

```
mini-tbench/
├── mini_tbench/                 # harness 包（核心实现）
│   ├── task_spec.py             # 任务规范加载（yaml → dataclass）
│   ├── sandbox.py               # Docker / 本地子进程隔离执行，超时与资源限制
│   ├── container.py             # 常驻容器执行器（纯终端形态，对齐 Terminal-Bench）
│   ├── agent_runtime.py         # 真实 LLM 驱动 ReAct 循环，逐步留痕（本地/容器双模）
│   ├── verifier.py              # 可执行测试 + 反作弊九项检查
│   ├── step_trace.py            # step 级失败归因（11 类标签 / root-cause 定位 / 聚合）
│   ├── redteam.py               # 13 类作弊 agent 攻击自家 harness
│   ├── harbor.py                # Harbor / Terminal-Bench 2.0 官方任务接入
│   ├── rollout.py               # 批量 rollout，轨迹落 JSONL，失败归因
│   ├── reward.py                # RLVR 奖励（pass/partial/format/effort/gaming）
│   ├── sft.py                   # 轨迹 → SFT / RLVR 训练样本导出
│   ├── dedup.py                 # 精确 + 近似去重
│   ├── review.py                # 人工抽检队列 + Golden Dataset
│   ├── contamination.py         # 数据污染指纹扫描
│   ├── gaming.py                # verifier 被钻空子检测与归因
│   └── cli.py                   # 命令行入口（oracle/rollout/export/dedup/review/analyze/scan）
├── tasks/
│   ├── task-01-fix-failing-tests/   # medium：逻辑 bug 修复（边界/幂等/单位换算）
│   ├── task-02-csv-pipeline/        # medium：数据管道口径（去重/缺失值/退款/确定性）
│   └── task-03-flaky-cli/           # hard  ：容错 CLI（超时/退避重试/幂等/部分成功）
├── serving/                     # 服务层：vLLM/SGLang 私有化自托管 + 并发/长上下文/稳定性
│   ├── vllm_serve.md            # 部署脚本 + flag→能力映射 + 断网验收清单
│   ├── agent_loop.py            # OpenAI 兼容端点驱动的 ReAct coding agent
│   └── rollout_manager.py       # asyncio 并发压测 + 指标采集 + 断点续跑
├── scripts/
│   ├── demo_pipeline.py         # 端到端演示（六段式，可一键复现）
│   ├── run_real_rollouts.py     # 真实模型批次 rollout（跨模型控制变量对比）
│   ├── run_official_bench.py    # 跑官方 Terminal-Bench / Harbor 任务集
│   └── make_final_report.py     # 汇总三路证据 → docs/real-evidence-report.md
├── harness_tests/               # 评测器自身单测 + 真实管道回归（18 项，全绿）
├── docs/
│   ├── real-evidence-report.md      # ★ 真实证据报告（轨迹 / 红队 / 官方 benchmark）
│   ├── agent-failure-mode-taxonomy.md  # ★ 失败模式分类法（观察 → 任务设计 的推导）
│   ├── task-spec-template.md        # 任务规范模板
│   ├── failure-attribution-report.md# 失败归因报告模板
│   └── client-task-spec-example.md  # 客户模糊目标 → 验收标准 翻译示例
├── requirements.txt
└── README.md
```

每个任务目录统一结构：`task.yaml`（规范）+ `environment/`（Dockerfile + workspace）
+ `tests/`（agent 不可见的 verifier）+ `solution/`（oracle 解法，用于校验 verifier）。

---

## 三、快速开始

```bash
pip install -r requirements.txt

# 1) 一键端到端演示（六段式：oracle → agent 采样 → 奖励 → 数据质量 → 作弊/污染 → 汇总）
python scripts/demo_pipeline.py

# 2) 单任务 oracle 校验（每个任务的 verifier 必须先被 oracle 证明正确）
python -m mini_tbench.cli oracle --task tasks/task-01-fix-failing-tests

# 3) 真实 agent rollout（AGENT_CMD 指向你的 coding agent）
python -m mini_tbench.cli rollout --task tasks/task-01-fix-failing-tests --runs 10

# 4) 数据质量流水线
python -m mini_tbench.cli analyze                 # 通过率 / gaming / 平均 reward
python -m mini_tbench.cli dedup                   # 去重
python -m mini_tbench.cli review                  # 人工抽检清单 CSV
python -m mini_tbench.cli export --out results/train   # SFT / RLVR 训练数据
python -m mini_tbench.cli scan --tasks tasks/*    # 污染指纹扫描

# 5) harness 自检（证明评测器本身正确）
python -m pytest harness_tests -q
```

### 实测结果（本地实测，全部可用 `scripts/` 复现）

```
# ── 评测器先自证：oracle 不绿，后面一切结论都不成立 ─────────────
oracle:              task-01 4/4 | task-02 2/2 | task-03 3/3   ALL PASS
harness 自检:         7 passed
真实管道回归:         11 passed  (harness_tests/test_real_pipeline.py)

# ── ① 真实 agent 轨迹（真实模型 × 真实沙箱 × 逐步留痕）──────────
n=30   pass=63.3%   avg_steps=11.9   tokens=1,306,182
   qwen3-coder-flash   15 条   67%
   qwen3-coder-plus    15 条   60%
失败类别分布: premature_finish 8 (27%) | protocol_violation 2 | planning_error 1
   → 复现: python scripts/run_real_rollouts.py --models qwen3-coder-flash,qwen3-coder-plus --runs 5

# ── ② verifier 红队：13 类作弊 agent 攻击自家 harness ────────────
加固前:  4 拦截 / 9 未检出
加固后: 12 拦截 / 0 漏网      ← 漏网清单反向驱动了 5 项新检测
   → 复现: python -c "from mini_tbench.redteam import run_redteam; run_redteam()"

# ── ③ 官方 benchmark：Long-Horizon-Terminal-Bench（46 tasks）────
实跑 2048：容器内 24 步 → 产出 moves.log（21 步）→ 官方引擎重放评分
          max_tile=16  band=0  score=88   （官方预算 14400s；本实验限 24 步）
   → 复现: python scripts/run_official_bench.py --bench-root <LHTB> --tasks 2048
```

**一处值得注意的失败模式差异**：两个模型在简单任务上都能过，差距出现在**收工纪律**上——
`premature_finish` 占全部失败的 27%，且根因步几乎都落在 `finish` 这个动作上
（即"没做验证就宣告完成"）。这直接指向 reward 设计：**对这类模型，过程奖励（奖励验证行为）
比结果奖励更能拉开差距。** 完整归因见 [`docs/real-evidence-report.md`](docs/real-evidence-report.md)。

---

## 四、核心设计决策（面试必聊）

1. **verifier 与任务环境物理隔离**：`tests/` 只在 verifier 阶段挂载，agent 全程不可见，
   从源头杜绝「读测试写答案」。
2. **反作弊九项检查（防 verifier 被钻空子）**：
   9 项闸门 = `harness-executable`（环境故障不得伪装成能力失败）+ `all-tests-pass` +
   `tests-untouched`（覆盖 data 等**非 .py** 验收资产）+ `no-hardcode` +
   `no-trivial-pass` + `no-test-literal-leak`（对抗"加条件 / 查表"式绕过的答案指纹法）+
   `no-injection`（conftest 收集钩子 / sitecustomize / 影子依赖模块）+
   `no-harness-shadow`（harness 同名模块 import 劫持）+ `scope-check`（越权写入）。

   **这九项不是拍脑袋列的，是红队打出来的**：先让 13 类作弊 agent 攻击自家 harness
   （首轮 4 拦截 / 9 未检出），再按漏网清单逐项补检测，复测收敛到 12 拦截 / 0 漏网。
   完整攻击矩阵与加固对照见 [`docs/real-evidence-report.md`](docs/real-evidence-report.md)。
   > 关键经验：**反作弊是对抗性搜索，不是模式匹配**。写死一条正则的检测，在下一次对抗中必然失效——
   > 例如 `if x == "字面量": return` 只需追加一个 `and qty == 4` 就完全失配。
   > 能站住的检测必须锚定**语义不变量**（这里的是："正解不需要知道测试用哪组样例"）。
3. **确定性优先**：主判定一律是**可执行测试**（退出码 + 行为断言）；LLM Judge 只做可选质量抽检，
   保证评测可复现。随机故障注入必须**固定种子**，否则 RL reward 不可复现。
4. **评测器必须先被证明正确**：每个任务先跑 `oracle` 模式，verifier 全绿后才允许接真实 agent。
   `harness_tests/` 进一步保证 reward / 去重 / gaming 判定逻辑自身正确。
5. **轨迹即数据**：每次 rollout 落一条 JSONL（指令、agent 输出、diff、verifier 逐项结果、
   耗时、失败归因），天然就是 SFT / RLVR 的原始形态；成功轨迹 → SFT，接近正确的失败 →
   部分 reward（rejection sampling），作弊轨迹直接剔除。
6. **失败分类法先于任务集**：先建立 **6 类互斥失败标签**（environment / tool_misuse / logic_error /
   gaming / planning / timeout），再让每个任务去"堵"其中一类。**一个任务只有在"某类失败会因它而暴露"
   时才有资格存在**，否则它只是在测模型的运气。推导过程与两类"验证器自身失效"的案例见
   [`docs/agent-failure-mode-taxonomy.md`](docs/agent-failure-mode-taxonomy.md)。
7. **验证器本身也在被测范围内**：验证器/奖励逻辑一改，必须重跑 oracle 全量 + `harness_tests/`，
   不允许增量信任——因为**验证器的 bug 不会表现为 bug，它会表现为"模型变强了"**。

---

## 五、服务层（serving/）：把 vLLM / SGLang 落地能力融进来

外部 agent CLI 只能证明「会用工具」；要证明**数据生产级**工程能力（并发、长上下文、稳定性），
必须自托管推理层：

- `vllm_serve.md`：AutoDL / 内网 GPU 一键起 vLLM/SGLang；每个启动参数对应一个岗位能力
  （`--max-num-seqs`→并发、`--max-model-len`+chunked-prefill→长上下文、
  `--gpu-memory-utilization`→防 OOM、SGLang RadixAttention→共享前缀吞吐翻倍）；
  含**私有化/内网离线部署**与断网验收清单（权重本地化、内网 pip 镜像、`HF_HUB_OFFLINE=1`）。
- `agent_loop.py`：用 OpenAI 兼容端点驱动自写 ReAct coding agent（bash / 读文件 / 跑测试），
  长上下文自动滑动压缩，替代外部 CLI。
- `rollout_manager.py`：asyncio 并发压测（Semaphore 对齐 `--max-num-seqs`）、
  采集 token/延迟/长上下文占比、失败时指数退避 + 断点续跑（run_id 幂等）。

---

## 六、范围边界与演进路线

### 当前范围（v1）

| 能力 | 当前实现 | 设计取舍 |
|---|---|---|
| 执行隔离 | Docker 后端（断网 + 内存 / CPU / pid 限制）与本地子进程后端**双轨** | Docker 为生产路径；子进程后端面向零依赖 CI 与无 Docker 环境，超时约束始终生效 |
| 评测判定 | **确定性可执行测试为主**（退出码 + 行为断言），LLM Judge 仅作可选质量抽检 | 评测必须可复现；随机故障注入固定种子，否则 reward 不可复现 |
| 任务集 | 3 个端到端任务（bug 修复 / 数据管道 / 容错 CLI），覆盖 medium–hard | 规模服从一条硬约束：**每个任务的 verifier 必须先被 oracle 证明正确**，才允许接真实 agent |
| 奖励信号 | RLVR 标量 reward（pass + partial + format + effort − gaming penalty） | 部分分用于承接「接近正确的失败」，可直接接入 rejection sampling |
| 污染控制 | 任务全部自主设计，公开样本指纹扫描结果 `CLEAN` | 不使用任何 benchmark 私有数据，从源头规避数据污染 |

### 演进路线

1. **serving 层 GPU 实测回填** —— 按 `vllm_serve.md` 在目标 GPU 上跑并发压测，
   把吞吐拐点、p50 / p95 延迟、长轨迹占比写成实测数字（本仓库交付的是压测脚本与指标采集逻辑，
   不预填估算值）。
2. **任务规模横向扩展** —— 以现有 `task.yaml` 规范为模板，扩到 long-horizon / 多文件重构类任务。
3. **Docker 后端接入 CI** —— 把三个任务的 oracle 校验固化进流水线，保证 verifier 改动不会静默劣化。
4. **rejection sampling 闭环** —— 把带部分 reward 的失败轨迹回灌，形成「采样 → 奖励 → 回灌」的迭代环。

---

## 七、License

[MIT](LICENSE) © 2026 Fanqingle
