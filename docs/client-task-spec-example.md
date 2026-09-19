# 客户交付示例：把模糊的能力目标翻译成任务规范与验收标准

> 对应 JD 职责6：「直接对接客户研发团队，把模糊的能力目标翻译成任务规范和验收标准」。
> 本文是一次真实工作流的浓缩：客户用口语描述「模型哪里不行」，我们把它变成
> **可构造、可执行、可评分、可反作弊**的任务规范。

---

## 一、客户原话（模糊的输入）

> 「我们的 coding agent 在长任务上有个毛病：它经常**做了一半就宣布自己完成了**，
> 而且改完不跑测试就说修好了。你们能不能帮我搞点数据，让它别这么早收工？」

这段话里有 3 个问题，都不能直接当任务写：

1. **「长任务」没有定义**——多少步算长？几个文件算长？
2. **「宣布完成」没有可观测的判据**——是没跑测试？还是跑了但没看结果？
3. **「别这么早收工」无法验收**——改善到什么程度算达标？

---

## 二、翻译过程（把形容词变成可测指标）

| 客户口语 | 消歧后的可测定义 | 落到任务规范的哪里 |
|---|---|---|
| 长任务 | 需要 ≥15 次工具调用、跨 ≥3 个文件修改 | `task.yaml → difficulty: hard / estimated_agent_steps: 15-25` |
| 宣布完成 | agent 停止动作时，仓库仍存在失败测试 | verifier rubric：`tests_must_pass_at_stop` |
| 没跑测试 | 轨迹中未出现测试执行工具调用 | 轨迹分析：`no_verification_attempt` 失败标签 |
| 别太早收工 | 在预算内（≤60s / ≤N 步）达到全测试通过 | rubric：`respects_budget` + `survives-fault-injection` |

> **经验法则**：客户的每句形容词，都要能对应到一个**可执行断言**或一个**可统计的轨迹标签**；
> 对应不上的，就是还没谈清楚的需求，必须回问客户，不能自己猜。

---

## 三、产出的任务规范（节选）

```yaml
id: task-xx-long-horizon
title: "在故障注入环境下完成跨文件重构并保证测试全绿"
difficulty: hard
category: long-horizon / robustness
estimated_agent_steps: 15-25

instruction: |
  /workspace 是一个多模块服务，现有 3 个失败测试（含跨模块依赖）。
  要求：在 60s 内让全部测试通过；不得修改 tests/；不得硬编码期望值。
  端点存在 40% 概率的随机故障（超时/5xx/截断），需具备重试与幂等。

rubric:
  - id: all-tests-pass        # executable：行为验收
    type: executable
  - id: tests-untouched       # anti-gaming：测试哈希
    type: anti-gaming
  - id: no-hardcode           # anti-gaming：期望字面量扫描
    type: anti-gaming
  - id: respects-budget       # executable：资源约束也是验收标准
    type: executable
```

---

## 四、验收标准的四个层次（本项目已全部落地）

1. **可执行验证**：`pytest` 行为断言（不能说谎）——`mini_tbench/verifier.py`
2. **反作弊校验**：测试哈希 / 硬编码扫描 / 恒真模式 ——`verifier.anti_cheat`
3. **资源与时间约束**：超时、内存/CPU 上限、断网 ——`mini_tbench/sandbox.py`
4. **轨迹级标签**：`no_verification_attempt`、`gaming`、`timeout` ——`rollout.py` 失败归因

---

## 五、交付给客户时附带的三样东西（缺一不可）

1. **任务规范**（`task.yaml`）—— 客户认可的业务语义与验收点
2. **可复现环境**（Dockerfile + workspace）—— 客户能自行重跑
3. **失败归因报告**（`docs/failure-attribution-report.md`）—— 证明数据「为什么有价值」，
   而不只是「有多少条」

---

## 六、避坑清单（真实踩过的）

- **别把客户的形容词直接写进 rubric**：「稳定」「高性能」无法执行，必须先量化。
- **随机性必须固定种子**：故障注入用固定 seed，否则 reward 不可复现，RL 直接废掉。
- **verifier 不能过松**：任何「恒真断言」都会变成 reward hacking 的入口，必须显式检测。
- **上下文要标注污染风险**：任务若与公开 benchmark 高度重合，必须做指纹比对后剔除。
