# 任务规范模板（写新任务时复制本文件）

## 为什么任务规范是核心交付物
post-training 数据岗的产出物不是「代码」而是「任务规范 + 验收标准」。规范写得越歧义少，
后续轨迹生成和 reward 构建越稳定。以下结构每条都对应一个高频踩坑点。

## 模板结构

```yaml
id: task-XX-short-name          # 全局唯一，用于轨迹关联
title: 一句话任务名
difficulty: easy|medium|hard    # 难度分层依据：预计 agent 步数、约束数量、故障注入强度
category: bugfix|feature|refactor|data-pipeline|robustness
estimated_agent_steps: N-M

instruction: |                  # agent 可见的完整指令（自包含，不留「你应该知道」）
  ...约束必须显式列出：禁改文件、禁硬编码、资源限制、验收口径...

rubric:                         # 验收标准：确定性检查优先，rubric 类检查仅作补充
  - id: <检查点id>
    type: executable            # 可执行测试（首选）
    type: anti-gaming           # 反作弊检查（必备：测试哈希/硬编码扫描/diff 范围）
    type: rubric                # 需 LLM Judge 或人工抽检的质量项
    desc: ...

design_notes: |                 # 元信息：为什么这么设计、陷阱在哪、轨迹预期用途
  - 难度分层依据
  - 陷阱设计（预期失败模式）
  - 成功轨迹 → SFT / 失败轨迹 → 归因与重采样
```

## 高频踩坑清单（来自真实 rollout）

1. **指令留白**：没写「禁止修改测试」→ agent 直接改测试让断言恒真。
   对策：反作弊三件套（哈希校验/硬编码扫描/diff 范围）做成所有任务默认继承。
2. **验收随机**：故障注入没固定种子 → 同一轨迹时过时不过，reward 不可复现。
   对策：所有随机性（故障序列、洗牌、端口）必须从 seed 派生。
3. **验证器耦合实现**：断言了内部函数名/私有结构 → 正确但风格不同的解法被判 FAIL。
   对策：只断言可观测行为（输出文件、exit code、外部接口）。
4. **任务无解或平凡解**：写完任务没先跑 oracle → verifier 全红或全绿。
   对策：CI 强制 oracle 先过 verifier；再加一条「空改动必须 FAIL」的反向校验。
5. **难度失真**：标注 medium 实际 90% agent 一步过 → 数据没有区分度。
   对策：rollout 成功率落在 30%–80% 区间的任务才是有效训练样本，按此回标 difficulty。
