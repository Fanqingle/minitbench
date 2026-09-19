"""任务规范加载（职责1：任务构造 / 难度分层 / rubric）。

任务目录约定：
  tasks/<id>/task.yaml          任务规范（指令 / 难度 / rubric / 设计笔记）
  tasks/<id>/environment/       Dockerfile + agent 初始工作区 workspace/
  tasks/<id>/tests/              verifier 测试（agent 不可见）
  tasks/<id>/solution/          oracle 解法（用于校验 verifier 本身正确）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # PyYAML
except Exception:  # pragma: no cover - 提供极简回退，仅解析本项目固定 schema
    yaml = None


@dataclass
class RubricItem:
    id: str
    type: str          # executable | anti-gaming | manual
    desc: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "RubricItem":
        return cls(id=d["id"], type=d.get("type", "executable"), desc=d.get("desc", ""))


@dataclass
class DesignNotes:
    difficulty_tier: str = "medium"
    trajectory_usage: str = ""
    pitfalls: list[str] = field(default_factory=list)


@dataclass
class Task:
    id: str
    title: str
    difficulty: str
    category: str
    instruction: str
    rubric: list[RubricItem] = field(default_factory=list)
    design_notes: DesignNotes = field(default_factory=DesignNotes)
    dir: Path | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def path(self, sub: str) -> Path:
        assert self.dir is not None
        return self.dir / sub

    @property
    def workspace_src(self) -> Path:
        return self.path("environment/workspace")

    @property
    def tests_dir(self) -> Path:
        return self.path("tests")

    @property
    def solution_dir(self) -> Path:
        return self.path("solution")


def _parse_simple(text: str) -> dict:
    """无 PyYAML 时的极简 YAML 解析，仅支持本仓库固定结构。"""
    # 仅用于离线兜底，不追求通用；生产环境应安装 PyYAML。
    out: dict[str, Any] = {}
    cur_rubric = None
    cur_design = None
    section = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
        if m and not line.startswith(" "):
            key, val = m.group(1), m.group(2).strip()
            if val == "":
                if key in ("rubric", "design_notes"):
                    section = key
                else:
                    section = None
                    out[key] = ""
            else:
                section = None
                out[key] = val.strip().strip('"')
            continue
        if line.startswith("  - id:"):
            if cur_rubric is None:
                cur_rubric = []
                out["rubric"] = cur_rubric
            item = {"id": line.split("id:")[1].strip().strip('"')}
            cur_rubric.append(item)
            continue
        # 续行属性
        am = re.match(r"^    ([A-Za-z_]+):\s*(.*)$", line)
        if am and cur_rubric:
            k, v = am.group(1), am.group(2).strip().strip('"')
            cur_rubric[-1][k] = v
    return out


def load_task(task_dir: str | Path) -> Task:
    task_dir = Path(task_dir).resolve()
    if not (task_dir / "task.yaml").exists():
        raise FileNotFoundError(task_dir / "task.yaml")
    text = (task_dir / "task.yaml").read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(text)
    else:  # pragma: no cover
        data = _parse_simple(text)
    rubric = [RubricItem.from_dict(r) for r in (data.get("rubric") or [])]
    dn_raw = data.get("design_notes")
    # 兼容两种写法：结构化 mapping，或 YAML 块标量字符串（`design_notes: |`）
    if isinstance(dn_raw, str):
        raw = dn_raw
        notes = DesignNotes(
            difficulty_tier=data.get("difficulty", "medium"),
            trajectory_usage=raw.strip(),
            pitfalls=[ln.strip(" -") for ln in raw.splitlines()
                      if ln.strip().startswith("-")],
        )
    else:
        dn = dn_raw or {}
        notes = DesignNotes(
            difficulty_tier=dn.get("difficulty_tier", data.get("difficulty", "medium")),
            trajectory_usage=dn.get("trajectory_usage", ""),
            pitfalls=dn.get("pitfalls", []) if isinstance(dn.get("pitfalls"), list) else [],
        )
    return Task(
        id=data["id"],
        title=data.get("title", data["id"]),
        difficulty=data.get("difficulty", "medium"),
        category=data.get("category", "coding"),
        instruction=data.get("instruction", "").strip(),
        rubric=rubric,
        design_notes=notes,
        dir=task_dir,
        meta=data,
    )
