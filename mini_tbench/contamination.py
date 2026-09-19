"""数据污染检测（职责4：识别数据污染）。

场景：任务/测试/解法若与公开 benchmark（Terminal-Bench / SWE-bench / GitHub）
高度重合，则该样本不能进入训练集——否则评测失真、reward hacking。

做法：
  - 离线指纹库：维护已知公开样本的 (file_sha256) 名单
  - 提交时扫描：对 tests/ 与 solution/ 计算指纹，命中即标记 contaminated
  - 近邻扫描：对候选语料目录做归一化相似度比对（可选，重活）
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ContaminationReport:
    task: str
    clean: bool
    hits: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def verdict(self) -> str:
        return "CLEAN" if self.clean else "CONTAMINATED"


class ContaminationScanner:
    def __init__(self, known_leaks: set[str] | None = None):
        # known_leaks：已知公开样本的指纹集合（离线维护，来自 SWE-bench/Terminal-Bench 等）
        self.known_leaks = known_leaks or set()

    def _fingerprint(self, d: Path) -> str:
        h = hashlib.sha256()
        for p in sorted(d.rglob("*.py")):
            if p.is_file():
                h.update(p.relative_to(d).as_posix().encode())
                h.update(p.read_bytes())
        return h.hexdigest()

    def scan_task(self, task) -> ContaminationReport:
        hits = []
        for name, sub in (("tests", task.tests_dir), ("solution", task.solution_dir)):
            fp = self._fingerprint(sub)
            if fp in self.known_leaks:
                hits.append(f"{name}:{fp[:12]}")
        return ContaminationReport(
            task=task.id, clean=not hits,
            hits=hits,
            note="命中离线指纹库" if hits else "未命中已知公开样本指纹库",
        )
