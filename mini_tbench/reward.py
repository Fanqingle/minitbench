"""奖励设计（职责1：rubric / verifier / reward 构建）。

把 verifier 结果映射为可加权的奖励信号，供 RLVR（Reward Modeling）使用：
  - pass_reward     ：全通过给 1.0，否则 0
  - partial_reward  ：通过的测试比例（鼓励「接近正确」的轨迹，rejection sampling 用）
  - format_reward   ：轨迹是否为结构化步骤，惩罚空输出
  - effort_reward   ：在限时内完成给小幅奖励，鼓励高效
  - gaming_penalty  ：触发反作弊任一项，整体奖励置负（防 reward hacking）
合计 reward 落在 [-1, 1]，可直接作为 RLVR 的 scalar reward。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Reward:
    pass_reward: float
    partial_reward: float
    format_reward: float
    effort_reward: float
    gaming_penalty: float
    total: float
    breakdown: dict

    @classmethod
    def compute(cls, verdict: str, tests_passed: int, tests_total: int,
                timed_out: bool, failure_tag: str, has_steps: bool) -> "Reward":
        pass_r = 1.0 if verdict == "PASS" else 0.0
        partial = (tests_passed / tests_total) if tests_total else 0.0
        fmt = 0.1 if has_steps else 0.0
        effort = 0.05 if (not timed_out) else -0.1
        gaming = -1.0 if failure_tag == "gaming" else 0.0
        total = pass_r + 0.3 * partial + fmt + effort + gaming
        total = max(-1.0, min(1.0, total))
        return cls(pass_r, partial, fmt, effort, gaming, total,
                   dict(pass_reward=pass_r, partial=round(0.3 * partial, 3),
                        fmt=fmt, effort=effort, gaming=gaming))


def compute_reward(verdict: str, tests_passed: int, tests_total: int,
                   timed_out: bool, failure_tag: str, has_steps: bool) -> Reward:
    """模块级别入口，供 cli / sft 调用。"""
    return Reward.compute(verdict, tests_passed, tests_total,
                          timed_out, failure_tag, has_steps)
