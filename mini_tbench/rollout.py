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

from .agent_runtime import AgentRun, run_agent
from .sandbox import SandboxResult, cp_solution_to_workspace, run_local
from .step_trace import attribute_failure
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
    # --- 真实 agent rollout（mode="react"）专属字段 ---
    model: str = ""
    stop_reason: str = ""
    n_steps: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)   # step 级轨迹（SFT 原料）
    attribution: dict[str, Any] | None = None                   # step 级失败归因

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


def _localize(instruction: str) -> str:
    """容器内路径 /workspace/x → 本地相对路径 x（agent 的 cwd 已是 workspace 根）。"""
    return (instruction.replace("/workspace/", "")
            .replace("/workspace", "the workspace root"))


def _step_summary(s) -> dict:
    """JSONL 里的 step 摘要（完整轨迹另存 trajectory.json，避免 JSONL 膨胀）。"""
    args = {}
    for k, v in (s.args or {}).items():
        sval = str(v)
        args[k] = (sval[:300] + f"...[{len(sval)} chars]") if len(sval) > 300 else sval
    return {"idx": s.idx, "thought": s.thought[:400], "tool": s.tool, "args": args,
            "observation": s.observation[:600], "error": s.error,
            "parse_failed": s.parse_failed, "elapsed": s.elapsed, "usage": s.usage}


def do_rollout(task: Task, run_id: str, *, mode: str = "agent",
               results_dir: Path | None = None,
               agent_cmd: str | None = None,
               agent_fn=None,
               timeout: int = 600, mem_limit_mb: int | None = None,
               local: bool = True,
               backend: str = "dashscope", model: str = "qwen3-coder-plus",
               base_url: str | None = None, max_steps: int = 24,
               temperature: float = 0.7,
               save_trajectory: bool = True) -> Trajectory:
    """执行一次 rollout。

    mode:
      react  —— 真实模型驱动真实 ReAct 循环（真实轨迹，产出 step 级归因）
      oracle —— 注入标准解法，用于校验 verifier 自身正确性
      agent  —— 子进程执行 agent_cmd（接任意第三方 agent CLI）
    """
    results_dir = results_dir or (Path(__file__).resolve().parents[2] / "results")
    run_dir = results_dir / run_id
    run_ws = run_dir / "workspace"
    if run_ws.exists():
        shutil.rmtree(run_ws)
    run_ws.mkdir(parents=True)

    # 初始化 workspace（拷贝 environment/workspace）
    if task.workspace_src.exists():
        shutil.copytree(task.workspace_src, run_ws, dirs_exist_ok=True)

    baseline = sha256_of_dir(task.tests_dir)
    agent_run: AgentRun | None = None

    if mode == "oracle":
        cp_solution_to_workspace(task.solution_dir, run_ws)
        sandbox = _ok_result()
    elif mode == "react":
        agent_run = run_agent(
            run_ws, _localize(task.instruction),
            backend=backend, model=model, base_url=base_url,
            max_steps=max_steps, temperature=temperature, total_timeout=timeout)
        sandbox = SandboxResult(
            ok=agent_run.ok, returncode=0 if agent_run.ok else 1,
            stdout=json.dumps({"stop_reason": agent_run.stop_reason,
                               "final_answer": agent_run.final_answer[:1500],
                               "usage": agent_run.usage_total}, ensure_ascii=False),
            stderr="; ".join(f"step{s.idx}:{s.error}" for s in agent_run.steps
                             if s.error)[:2000],
            elapsed=agent_run.elapsed,
            timed_out=agent_run.stop_reason == "timeout", backend="react")
    elif agent_fn is not None:
        # 以函数注入 agent 行为（可复现演示：gaming / no-op / 正确解法）
        res = agent_fn(run_ws)
        sandbox = res or _ok_result()
    else:
        cmd = agent_cmd or "python -c \"raise SystemExit(1)\""  # 默认必定失败
        sandbox = run_local(run_ws, cmd, timeout=timeout, mem_limit_mb=mem_limit_mb)

    ver = run_verifier(task, run_ws, baseline_sha=baseline)
    checks = [{"id": c.id, "passed": c.passed, "detail": c.detail} for c in ver.checks]

    if agent_run is not None:
        attr = attribute_failure(run_id, agent_run, ver.verdict, checks)
        tag = attr.category
        if save_trajectory:
            (run_dir / "trajectory.json").write_text(
                json.dumps(agent_run.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8")
        steps = [_step_summary(s) for s in agent_run.steps]
        model_name, stop_reason = agent_run.model, agent_run.stop_reason
        n_steps, usage = len(agent_run.steps), agent_run.usage_total
        attribution = attr.to_dict()
    else:
        tag = _failure_tag(ver, sandbox)
        steps, model_name, stop_reason = [], "", ""
        n_steps, usage, attribution = 0, {}, None

    diff = _diff_workspace(task.workspace_src, run_ws)
    return Trajectory(
        run_id=run_id, task=task.id, mode=mode,
        ts=dt.datetime.now().isoformat(),
        instruction=task.instruction,
        agent_stdout=sandbox.stdout[:4000], agent_stderr=sandbox.stderr[:2000],
        agent_elapsed=sandbox.elapsed, timed_out=sandbox.timed_out,
        verdict=ver.verdict, tests_passed=ver.tests_passed,
        tests_total=ver.tests_total, failure_tag=tag, diff=diff,
        model=model_name, stop_reason=stop_reason, n_steps=n_steps,
        usage=usage, steps=steps, attribution=attribution,
        meta={"checks": checks},
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
                  out_jsonl: Path | None = None,
                  save_trajectory: bool = True,
                  **agent_kw) -> list[Trajectory]:
    """批量 rollout。mode="react" 时把 backend/model/max_steps 等经 **agent_kw 透传。"""
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
                           agent_cmd=agent_cmd, save_trajectory=save_trajectory,
                           **agent_kw)
            f.write(t.to_jsonl() + "\n")
            f.flush()
            trajs.append(t)
            attr = t.attribution or {}
            print(f"[{rid}] {t.verdict} cat={t.failure_tag} "
                  f"steps={t.n_steps} root@{attr.get('root_cause_step')} "
                  f"t={t.agent_elapsed}s", flush=True)
    return trajs
