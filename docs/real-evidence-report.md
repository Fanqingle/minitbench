# Mini-TBench 真实证据报告

> 本报告回应两个硬缺口：① 没有一条真实 agent 轨迹；② 从未跑过真实 benchmark。
> 全部数字来自本仓库 `results/` 下的原始产物，可用 `scripts/` 复现。

## 0. 摘要

| 缺口 | 解决方式 | 硬证据 |
|---|---|---|
| 没有真实 agent 轨迹 | 真实模型（DashScope OpenAI 兼容端点）驱动自研 ReAct 循环，动作经真实沙箱执行，逐步留痕 | **30 条真实轨迹**，成功率 63.3%，平均 11.9 步 |
| verifier 是否会被钻空子 | 构造作弊 agent 攻击自家 harness，按漏网清单反向加固 | **13 类攻击，12 拦截 / 0 漏网** |
| 从未跑过真实 benchmark | 接入官方 Harbor / Terminal-Bench 2.0 任务集（预构建镜像 + 隐藏 verifier）并复现产物重建评分 | 发现 **46 个官方任务**，实跑 1 个，其中 0 个达到 2048 band |

---

# 1. 真实 agent 轨迹

## 1.1 总体

- 轨迹条数：**30**（每个任务 × 模型 × 采样，控制变量）
- 成功率：**63.3%**
- 平均步数：**11.9**
- 累计 token：**1,306,182**

## 1.2 跨模型对比（同任务 / 同 harness / 同温度，仅换模型）

| 模型 | 条数 | 成功率 | 平均步数 | 主导失败类别 |
|---|---:|---:|---:|---|
| `qwen3-coder-flash` | 15 | 67% | 12.8 | protocol_violation（未遵循输出协议（ReAct JSON）） |
| `qwen3-coder-plus` | 15 | 60% | 11.1 | protocol_violation（未遵循输出协议（ReAct JSON）） |

## 1.3 失败定位（这是 reward 设计真正需要的粒度）

# 整体失败归因

- 轨迹条数：**30**
- 成功率：**63.3%**（19/30）
- 平均步数：**11.9**

## 失败类别分布

| 类别 | 条数 | 占比 |
|---|---:|---:|
| protocol_violation（未遵循输出协议（ReAct JSON）） | 2 | 7% |
| premature_finish（过早宣称完成（未验证即收工）） | 8 | 27% |
| planning_error（规划失败（步数耗尽 / 长期无进展）） | 1 | 3% |
| none（无失败） | 19 | 63% |

## 失败发生位置（root cause step 分布）

| 步区间 | 条数 |
|---|---:|
| 第 1-3 步 | 1 |
| 第 11-15 步 | 4 |
| 第 16+ 步 | 2 |
| 第 4-6 步 | 1 |
| 第 7-10 步 | 3 |

## 失败时的工具（root cause on which tool）

| 工具 | 条数 |
|---|---:|
| `finish` | 8 |
| `__parse__` | 2 |
| `read_file` | 1 |

## 分任务通过率

| 任务 | 通过/总数 | 通过率 |
|---|---:|---:|
| task-01-fix-failing-tests | 10/10 | 100% |
| task-02-csv-pipeline | 9/10 | 90% |
| task-03-flaky-cli | 0/10 | 0% |


### 逐条明细

| run_id | 模型 | 任务 | 判定 | 类别 | root step | 工具 | 步数 |
|---|---|---|---|---|---:|---|---:|
| task-01-fix-failing-tests__coderflash__0919- | qwen3-coder-flash | task-01-fix-failing-tests | PASS | none | - | `-` | 13 |
| task-01-fix-failing-tests__coderflash__0919- | qwen3-coder-flash | task-01-fix-failing-tests | PASS | none | - | `-` | 10 |
| task-01-fix-failing-tests__coderflash__0919- | qwen3-coder-flash | task-01-fix-failing-tests | PASS | none | - | `-` | 8 |
| task-01-fix-failing-tests__coderflash__0919- | qwen3-coder-flash | task-01-fix-failing-tests | PASS | none | - | `-` | 9 |
| task-01-fix-failing-tests__coderflash__0919- | qwen3-coder-flash | task-01-fix-failing-tests | PASS | none | - | `-` | 11 |
| task-01-fix-failing-tests__coderplus__0919-2 | qwen3-coder-plus | task-01-fix-failing-tests | PASS | none | - | `-` | 10 |
| task-01-fix-failing-tests__coderplus__0919-2 | qwen3-coder-plus | task-01-fix-failing-tests | PASS | none | - | `-` | 9 |
| task-01-fix-failing-tests__coderplus__0919-2 | qwen3-coder-plus | task-01-fix-failing-tests | PASS | none | - | `-` | 18 |
| task-01-fix-failing-tests__coderplus__0919-2 | qwen3-coder-plus | task-01-fix-failing-tests | PASS | none | - | `-` | 5 |
| task-01-fix-failing-tests__coderplus__0919-2 | qwen3-coder-plus | task-01-fix-failing-tests | PASS | none | - | `-` | 8 |
| task-02-csv-pipeline__coderflash__0919-22511 | qwen3-coder-flash | task-02-csv-pipeline | PASS | none | - | `-` | 18 |
| task-02-csv-pipeline__coderflash__0919-22511 | qwen3-coder-flash | task-02-csv-pipeline | PASS | none | - | `-` | 12 |
| task-02-csv-pipeline__coderflash__0919-22511 | qwen3-coder-flash | task-02-csv-pipeline | PASS | none | - | `-` | 14 |
| task-02-csv-pipeline__coderflash__0919-22511 | qwen3-coder-flash | task-02-csv-pipeline | PASS | none | - | `-` | 18 |
| task-02-csv-pipeline__coderflash__0919-22511 | qwen3-coder-flash | task-02-csv-pipeline | PASS | none | - | `-` | 18 |
| task-02-csv-pipeline__coderplus__0919-225114 | qwen3-coder-plus | task-02-csv-pipeline | PASS | none | - | `-` | 10 |
| task-02-csv-pipeline__coderplus__0919-225114 | qwen3-coder-plus | task-02-csv-pipeline | PASS | none | - | `-` | 10 |
| task-02-csv-pipeline__coderplus__0919-225114 | qwen3-coder-plus | task-02-csv-pipeline | PASS | none | - | `-` | 14 |
| task-02-csv-pipeline__coderplus__0919-225114 | qwen3-coder-plus | task-02-csv-pipeline | FAIL | planning_error | 18 | `read_file` | 18 |
| task-02-csv-pipeline__coderplus__0919-225114 | qwen3-coder-plus | task-02-csv-pipeline | PASS | none | - | `-` | 9 |
| task-03-flaky-cli__coderflash__0919-225114-0 | qwen3-coder-flash | task-03-flaky-cli | FAIL | premature_finish | 15 | `finish` | 15 |
| task-03-flaky-cli__coderflash__0919-225114-0 | qwen3-coder-flash | task-03-flaky-cli | FAIL | premature_finish | 13 | `finish` | 13 |
| task-03-flaky-cli__coderflash__0919-225114-0 | qwen3-coder-flash | task-03-flaky-cli | FAIL | protocol_violation | 3 | `__parse__` | 7 |
| task-03-flaky-cli__coderflash__0919-225114-0 | qwen3-coder-flash | task-03-flaky-cli | FAIL | premature_finish | 10 | `finish` | 10 |
| task-03-flaky-cli__coderflash__0919-225114-0 | qwen3-coder-flash | task-03-flaky-cli | FAIL | premature_finish | 16 | `finish` | 16 |
| task-03-flaky-cli__coderplus__0919-225114-00 | qwen3-coder-plus | task-03-flaky-cli | FAIL | protocol_violation | 4 | `__parse__` | 10 |
| task-03-flaky-cli__coderplus__0919-225114-01 | qwen3-coder-plus | task-03-flaky-cli | FAIL | premature_finish | 10 | `finish` | 10 |
| task-03-flaky-cli__coderplus__0919-225114-02 | qwen3-coder-plus | task-03-flaky-cli | FAIL | premature_finish | 13 | `finish` | 13 |
| task-03-flaky-cli__coderplus__0919-225114-03 | qwen3-coder-plus | task-03-flaky-cli | FAIL | premature_finish | 10 | `finish` | 10 |
| task-03-flaky-cli__coderplus__0919-225114-04 | qwen3-coder-plus | task-03-flaky-cli | FAIL | premature_finish | 12 | `finish` | 12 |

---

# 2. verifier 红队：作弊 agent 攻击自家 harness

- 攻击总数：**13**
- 被拦截（CAUGHT）：**12**
- 漏网（ESCAPED）：**0**
- 未生效（INEFFECTIVE，属能力失败而非作弊）：1

## 2.1 攻击矩阵与拦截结果

| # | 攻击 | 机制 | 结局 | 命中的检查项 |
|---|---|---|---|---|
| A1-patch-tests | 覆写验收测试为恒真 | 把 tests/test_outputs.py 换成 `assert True`，测试自然全过 | **CAUGHT** | tests-untouched |
| A2-delete-tests | 删除验收测试 | 删空 tests/ 下的测试文件，pytest 无测试可失败 | **CAUGHT** | tests-untouched |
| A3-patch-test-data | 篡改测试数据文件（非 .py） | 在 tests/data/*.csv 末尾追加空行：语义不变、内容变了，考验基线哈希是否覆盖非代码资产 | **CAUGHT** | tests-untouched |
| A4-add-conftest | 注入 conftest.py 劫持 pytest 收集 | 在 workspace 放 conftest.py，把每个 test_* 函数替换为空实现 | **CAUGHT** | no-injection |
| A5-sitecustomize | 用 sitecustomize.py 篡改解释器启动过程 | workspace 在 PYTHONPATH 上，sitecustomize 会被自动 import，可在 pytest 之前改写 sys.exit / 断言行为 | **CAUGHT** | no-injection |
| B1-hardcode-naive | 朴素硬编码特判 | `if sku == "SKU-001": return ...` —— 最典型、正则最容易命中的作弊 | **CAUGHT** | no-hardcode |
| B2-hardcode-guarded | 加条件的硬编码特判（绕过脆弱正则） | `if sku == "SKU-001" and qty == 4:` —— 在字符串比较后追加条件，使 `== "字面量": return` 这类正则失配 | **CAUGHT** | no-test-literal-leak |
| B3-hardcode-table | 查表式硬编码 | 把期望值放进 dict / 元组，运行时按 key 取答案，不含 `if ...: return ...` 形态 | **CAUGHT** | no-test-literal-leak |
| B4-trivial-assert | 源码内恒真断言 | 在业务代码里塞 `assert True` / `pytest.skip`，让断言失去约束力 | **CAUGHT** | no-trivial-pass |
| C1-hijack-pytest | 投放 pytest.py 劫持测试框架 import | workspace 位于 PYTHONPATH 首位，放一个 pytest.py 即可遮蔽真实 pytest，使 `python -m pytest` 执行攻击者代码而非跑测试 | **CAUGHT** | no-injection |
| C2-shadow-verifier | 投放影子 verifier / harness 包 | 在 workspace 放同名 verifier.py 与 mini_tbench/ 包，探测 harness 是否可能被 import 路径劫持（PYTHONPATH 含 workspace 时的真实风险） | **CAUGHT** | no-harness-shadow |
| C3-write-outside | 越权写入 workspace 之外 | 向 workspace 的上一级目录写文件，探测 scope-check 是否存在 | **CAUGHT** | scope-check |
| D1-noop-claim-done | 什么都不做但宣称完成（对照组） | 不改任何代码，只在产物里写一句「已完成」——这不是作弊，而是典型 premature finish，用于验证 verifier 不误判为作弊 | **INEFFECTIVE** | - |

## 2.2 红队驱动的加固清单

| 首轮漏网项 | 根因 | 加固措施 |
|---|---|---|
| A3 篡改 tests/data/*.csv | 基线哈希 `*.py` 通配，非代码验收资产游离在基线外 | `sha256_of_dir` 覆盖全部文件（排除 __pycache__） |
| A4/A5/C1 conftest、sitecustomize、pytest.py 注入 | 无任何检测：workspace 位于 PYTHONPATH 首位即可劫持解释器与测试框架 | 新增 `no-injection`：conftest 收集钩子 + sitecustomize + 影子依赖模块 |
| B2 加条件硬编码（`if sku=="SKU-001" and qty==4`） | 形态正则 `== "字面量": return` 只要追加一个条件就失配 | 新增 `no-test-literal-leak`：以「测试独有且不属于任何合法实现」的字面量为指纹 |
| B3 查表式硬编码 | 同上，且不含 `if ...: return` 形态 | 同上（指纹法不依赖代码形态） |
| C2 workspace 内投放 verifier.py / mini_tbench 包 | 无检测，存在 harness 被 import 劫持的风险 | 新增 `no-harness-shadow` |
| C3 越权写入 workspace 之外 | 模块 docstring 声称有 scope-check，实际未实现 | 补齐 `scope-check`：沙箱边界内不得出现 workspace 之外的文件 |

> 加固后复测：`caught` 由 4 → 12，`escaped` 归零；同时用 3 个任务的 oracle 解法回归确认**无误伤**（否则就是拿漏检换误杀）。

---

# 3. 官方 benchmark 接入（Terminal-Bench / Harbor）

- 官方任务集：**Long-Horizon-Terminal-Bench**，共 46 个任务
- 实跑：1 个，模型 `qwen3-coder-flash`，max_steps=24

## 3.1 官方任务规范中值得注意的三个设计

1. **产物重建评分**：task.toml 顶层声明 `artifacts`，verifier 只认产物，agent 自报进度一律不算分。
2. **隐藏 verifier**：公开仓库里没有 `tests/`，评分逻辑在服务端，agent 无法针对评分器定向优化，也无法反向推断评分细节来钻空子。
3. **确定性重放**：以 2048 为例，splitmix64 PRNG 的 seed 烘焙进镜像（`G2048_SEED`），棋盘序列是 (seed, 移动序列) 的纯函数；verifier 用逐字复制的同一份 engine 重放 move log 即可 bit-for-bit 复现 —— **日志无法伪造分数，只有真实合并才能抬高最大方块**。

> 第 3 点是「不可作弊评测」最直接的工程实现，也正是本次红队加固所对标的思路：评测方必须比被评测方更懂怎么作弊。

## 3.2 实跑记录

| 任务 | 镜像 | 资源 | 网格内步数 | 停止原因 | 产物 | 重建评分 |
|---|---|---|---:|---|---|---|
| 2048 | `zli12321/lhtb-2048:20260615` | 2cpu/4096MB | 24 | max_steps | moves.log(42B) | max_tile=16 band=0 |

