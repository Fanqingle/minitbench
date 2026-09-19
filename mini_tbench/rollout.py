"""批量 rollout 与轨迹采集（职责2·3）：构建 → 执行 → 收集 → verifier → 落 JSONL。

每次 rollout 生成一条 Trajectory，天然就是 SFT/RLVR 数据的原始形态：
  { run_id, task, mode, instruction, steps, diff, verification, timing, failure_tag }
失败归因标签：{ env_setup | tool_misuse | logic_error | gaming | timeout | none }
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .sandbox import cp_solution_to_workspace, run_local
from .task_spec import Task, load_task
from .verifier import sha256_of_dir, run_verifier


@dataclass
class Trajectory:
    run_id: str
    task: str
    mode: str
    ts: str
    instruction: str
    agent_stdout: str = ""
    agent_stderr: str = ""
    agent_elapsed: float = 0.0
    timed_out: bool = False
    verdict: str = "FAIL"
    tests_passed: int = 0
    tests_total: int = 0
    failure_tag: str = "none"
    diff: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def _failure_tag(ver: Any, sandbox) -> str:
    if ver.verdict == "PASS":
        return "none"
    if sandbox.timed_out:
        return "timeout"
    for c in ver.checks:
        if not c.passed and c.id in ("no-hardcode", "no-trivial-pass", "tests-untouched"):
            return "gaming"
    # 测试失败但非作弊 -> 语义/逻辑或工具误用（具体由 gaming 分析进一步细分）
    return "logic_error"


def do_rollout(task: Task, run_id: str, *, mode: str = "agent",
               results_dir: Path | None = None,
               agent_cmd: str | None = None,
               agent_fn=None,
               timeout: int = 600, mem_limit_mb: int | None = None,
               local: bool = True) -> Trajectory:
    results_dir = results_dir or (Path(__file__).resolve().parents[2] / "results")
    run_ws = results_dir / run_id / "workspace"
    if run_ws.exists():
        shutil.rmtree(run_ws)
    run_ws.mkdir(parents=True)

    # 初始化 workspace（拷贝 environment/workspace）
    if task.workspace_src.exists():
        shutil.copytree(task.workspace_src, run_ws, dirs_exist_ok=True)

    baseline = sha256_of_dir(task.tests_dir)

    if mode == "oracle":
        cp_solution_to_workspace(task.solution_dir, run_ws)
        sandbox = _ok_result()
    elif agent_fn is not None:
        # 以函数注入 agent 行为（可复现演示：gaming / no-op / 正确解法）
        res = agent_fn(run_ws)
        sandbox = res or _ok_result()
    else:
        cmd = agent_cmd or "python -c \"raise SystemExit(1)\""  # 默认必定失败
        sandbox = run_local(run_ws, cmd, timeout=timeout, mem_limit_mb=mem_limit_mb)

    ver = run_verifier(task, run_ws, baseline_sha=baseline)
    tag = _failure_tag(ver, sandbox)
    # 记录 diff（workspace 相对初始的改动摘要）
    diff = _diff_workspace(task.workspace_src, run_ws)
    return Trajectory(
        run_id=run_id, task=task.id, mode=mode,
        ts=dt.datetime.now().isoformat(),
        instruction=task.instruction,
        agent_stdout=sandbox.stdout[:4000], agent_stderr=sandbox.stderr[:2000],
        agent_elapsed=sandbox.elapsed, timed_out=sandbox.timed_out,
        verdict=ver.verdict, tests_passed=ver.tests_passed,
        tests_total=ver.tests_total, failure_tag=tag, diff=diff,
        meta={"checks": [{"id": c.id, "passed": c.passed, "detail": c.detail}
                         for c in ver.checks]},
    )


def _ok_result():
    """构造一个「agent 正常退出」的占位 SandboxResult。"""
    from .sandbox import SandboxResult
    return SandboxResult(ok=True, returncode=0, elapsed=0.0)


def _diff_workspace(src: Path, cur: Path) -> str:
    if not src.exists():
        return ""
    lines = []
    for p in sorted(cur.rglob("*.py")):
        rel = p.relative_to(cur)
        s = src / rel
        if s.exists():
            if s.read_bytes() != p.read_bytes():
                lines.append(f"~ {rel}")
        else:
            lines.append(f"+ {rel}")
    for p in src.rglob("*.py"):
        if not (cur / p.relative_to(src)).exists():
            lines.append(f"- {p.relative_to(src)}")
    return "\n".join(lines)[:1500]


def batch_rollout(task_dir: str | Path, *, runs: int = 10, mode: str = "agent",
                  results_dir: Path | None = None,
                  agent_cmd: str | None = None,
                  out_jsonl: Path | None = None) -> list[Trajectory]:
    task = load_task(task_dir)
    results_dir = results_dir or (Path(__file__).resolve().parents[2] / "results")
    out_jsonl = out_jsonl or (results_dir / "results.jsonl")
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    trajs = []
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    with open(out_jsonl, "a", encoding="utf-8") as f:
        for i in range(runs):
            rid = f"{task.id}-{stamp}-{i:02d}"
            t = do_rollout(task, rid, mode=mode, results_dir=results_dir,
                           agent_cmd=agent_cmd)
            f.write(t.to_jsonl() + "\n")
            trajs.append(t)
            print(f"[{rid}] {t.verdict} tag={t.failure_tag} "
                  f"tests={t.tests_passed}/{t.tests_total} t={t.agent_elapsed}s")
    return trajs
