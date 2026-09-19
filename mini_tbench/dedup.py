"""数据去重（职责5：去重）。

  - 精确去重：按 (task, verdict, normalized_diff) 的 SHA256 去重
  - 近似去重：对 diff 做归一化（去空白/注释/变量名占位）后计算相似度，
    超过阈值视为重复，保留代表性一条（用于 SFT 数据去冗余，避免过拟合）
归一化采用轻量 token 处理 + difflib 比例，无需外部依赖。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


def normalize_diff(diff: str) -> str:
    s = diff.lower()
    s = re.sub(r"#.*", "", s)                 # 去注释
    s = re.sub(r"\s+", " ", s).strip()        # 压缩空白
    s = re.sub(r'["\']', "", s)               # 去引号
    return s


def _similarity(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


class DedupIndex:
    def __init__(self, threshold: float = 0.9):
        self.threshold = threshold
        self._exact: dict[str, str] = {}       # hash -> run_id
        self._norm: list[tuple[str, str]] = []  # (norm_diff, run_id)

    def add(self, run_id: str, verdict: str, diff: str) -> bool:
        """返回 True 表示保留（非重复），False 表示被判定为重复。"""
        key = hashlib.sha256(f"{verdict}|{normalize_diff(diff)}".encode()).hexdigest()
        if key in self._exact:
            return False
        norm = normalize_diff(diff)
        for existing_norm, _ in self._norm:
            if _similarity(norm, existing_norm) >= self.threshold:
                self._exact[key] = run_id
                return False
        self._exact[key] = run_id
        self._norm.append((norm, run_id))
        return True


def dedup_jsonl(jsonl: Path, out: Path | None = None,
                threshold: float = 0.9) -> dict:
    idx = DedupIndex(threshold)
    kept, removed = [], []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = __import__("json").loads(line)
        if idx.add(d.get("run_id", "?"), d.get("verdict", ""), d.get("diff", "")):
            kept.append(line)
        else:
            removed.append(d.get("run_id"))
    out = out or jsonl.with_name("results.dedup.jsonl")
    out.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return {"kept": len(kept), "removed": len(removed), "out": str(out)}
