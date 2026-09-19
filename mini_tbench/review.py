"""人工抽检与 Golden Dataset（职责5：人工抽检流程）。

  - ReviewQueue：从 results.jsonl 中筛出「需人工确认」的轨迹
    （FAIL 且非纯作弊、或 PASS 但触发可疑模式），导出为待审清单（CSV/markdown）
  - GoldenDataset：通过人工/自动双签的轨迹，沉淀为高质量验收基准，
    并反向校验 verifier 是否过松/过严（数据质量闭环）
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ReviewItem:
    run_id: str
    task: str
    verdict: str
    failure_tag: str
    reason: str
    flagged: bool = True


class ReviewQueue:
    def __init__(self, jsonl: Path):
        self.jsonl = jsonl

    def collect(self, suspicious_patterns: list[str] | None = None) -> list[ReviewItem]:
        suspicious_patterns = suspicious_patterns or ["skip", "return 5", "assert True"]
        items: list[ReviewItem] = []
        for line in self.jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            # FAIL 非作弊 → 需人判定是真实失败还是 verifier 误杀
            if d.get("verdict") == "FAIL" and d.get("failure_tag") != "gaming":
                items.append(ReviewItem(d["run_id"], d["task"], "FAIL",
                                         d.get("failure_tag", ""), "需人工判定根因"))
            # PASS 但 stdout 含可疑模式 → 抽查是否有侥幸通过
            elif d.get("verdict") == "PASS":
                blob = (d.get("agent_stdout", "") + d.get("diff", "")).lower()
                hit = [p for p in suspicious_patterns if p.lower() in blob]
                if hit:
                    items.append(ReviewItem(d["run_id"], d["task"], "PASS",
                                             "", f"可疑模式抽查: {hit}"))
        return items

    def export_csv(self, out: Path) -> int:
        items = self.collect()
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["run_id", "task", "verdict", "failure_tag", "reason", "reviewed", "signoff"])
            for it in items:
                w.writerow([it.run_id, it.task, it.verdict, it.failure_tag, it.reason, "", ""])
        return len(items)


class GoldenDataset:
    """通过双签的轨迹沉淀为 Golden Dataset（验收基准）。"""

    def __init__(self, path: Path):
        self.path = path
        self.records: list[dict[str, Any]] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.records.append(json.loads(line))

    def add(self, traj, human_signoff: bool, auto_signoff: bool) -> bool:
        if not (human_signoff and auto_signoff and traj.verdict == "PASS"):
            return False
        self.records.append({
            "task": traj.task, "run_id": traj.run_id,
            "diff": traj.diff, "human_signoff": True, "auto_signoff": True,
        })
        return True

    def save(self) -> None:
        self.path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in self.records) + "\n",
                             encoding="utf-8")

    def __len__(self) -> int:
        return len(self.records)
