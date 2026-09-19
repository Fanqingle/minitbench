"""训练数据导出（职责1·5）：从轨迹抽取 SFT / RLVR 样本。

  - SFT    ：仅成功轨迹 —— 指令 + agent 结构化步骤 → (prompt, response)
  - RLVR   ：成功轨迹带 reward=1；失败但非作弊的轨迹（接近正确）带部分 reward，
             作为 rejection sampling / 课程学习的素材；gaming 轨迹默认剔除（不可学）。
导出 schema 兼容主流训练框架（messages 列表 + 可选 reward）。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .reward import Reward


@dataclass
class Sample:
    task: str
    kind: str                       # sft | rlvr
    messages: list[dict] = field(default_factory=list)
    reward: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def extract_sft(traj) -> Sample | None:
    """成功轨迹 → 单轮 SFT 样本（指令为 user，agent 行为摘要为 assistant）。"""
    if traj.verdict != "PASS":
        return None
    messages = [
        {"role": "system", "content": "你是 coding agent，仅修改 /workspace 内文件满足指令。"},
        {"role": "user", "content": traj.instruction},
        {"role": "assistant", "content": _summarize_steps(traj)},
    ]
    return Sample(task=traj.task, kind="sft", messages=messages,
                  meta={"run_id": traj.run_id, "diff": traj.diff})


def extract_rlvr(traj, reward: Reward) -> Sample:
    messages = [
        {"role": "system", "content": "你是 coding agent，仅修改 /workspace 内文件满足指令。"},
        {"role": "user", "content": traj.instruction},
        {"role": "assistant", "content": _summarize_steps(traj)},
    ]
    return Sample(task=traj.task, kind="rlvr", messages=messages,
                  reward=reward.total,
                  meta={"run_id": traj.run_id, "verdict": traj.verdict,
                        "failure_tag": traj.failure_tag, "reward_breakdown": reward.breakdown})


def _summarize_steps(traj) -> str:
    """把 agent 输出压缩为结构化步骤（面试亮点：轨迹即数据）。"""
    head = traj.agent_stdout.strip().splitlines()[:12]
    body = "\n".join(head) if head else "(no stdout; diff shown below)"
    return f"{body}\n\n# diff\n{traj.diff}".strip()


def export_from_jsonl(jsonl: Path, out_dir: Path) -> dict:
    """读 results.jsonl，生成 train_sft.jsonl / train_rlvr.jsonl。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    sft_lines, rlvr_lines = [], []
    from .rollout import Trajectory
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        t = Trajectory(**{k: v for k, v in d.items() if k in Trajectory.__dataclass_fields__})
        reward = Reward.compute(t.verdict, t.tests_passed, t.tests_total,
                                t.timed_out, t.failure_tag, bool(t.agent_stdout.strip()))
        if t.verdict == "PASS":
            s = extract_sft(t)
            if s:
                sft_lines.append(s.to_jsonl())
            rlvr_lines.append(extract_rlvr(t, reward).to_jsonl())
        elif t.failure_tag != "gaming" and reward.partial_reward > 0:
            # 接近正确的失败轨迹：部分 reward，用于课程/拒绝采样
            rlvr_lines.append(extract_rlvr(t, reward).to_jsonl())
    (out_dir / "train_sft.jsonl").write_text("\n".join(sft_lines) + "\n", encoding="utf-8")
    (out_dir / "train_rlvr.jsonl").write_text("\n".join(rlvr_lines) + "\n", encoding="utf-8")
    return {"sft": len(sft_lines), "rlvr": len(rlvr_lines)}
