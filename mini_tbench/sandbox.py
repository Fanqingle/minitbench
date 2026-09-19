"""可复现执行环境（职责2·3）：容器隔离 + 超时与资源限制 + 批量 rollout 基座。

两种后端：
  - docker：生产路径，网络/进程/文件系统隔离，硬性内存与 CPU 限制
  - local ：本地子进程（仅用于无 Docker 环境验证；超时生效，内存上限在 Windows 上退化为软提示）
对 agent 全程只挂载 workspace + instruction，tests/ 与 solution/ 在 verifier 阶段才挂入，
从源头杜绝「读测试写答案」。
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SandboxResult:
    ok: bool
    returncode: int
    stdout: str = ""
    stderr: str = ""
    elapsed: float = 0.0
    timed_out: bool = False
    backend: str = "local"


def run_in_docker(image: str, agent_cmd: str, *, mem_limit: str = "1g",
                  cpus: str = "2", timeout: int = 600,
                  mount_task_yaml: Path | None = None,
                  export_workspace_to: Path | None = None) -> SandboxResult:
    """在隔离容器中执行 agent 命令。"""
    container = f"mtb-run-{int(time.time()*1000)}"
    parts = [
        "docker", "run", "--name", container, "--rm",
        "--network", "none",                 # 断网：禁止 agent 联网抄答案
        "--memory", mem_limit, "--cpus", cpus,
        f"--pids-limit", "256",             # 进程数上限
        "--cap-drop", "ALL",                # 降权
    ]
    if mount_task_yaml:
        parts += ["-v", f"{mount_task_yaml}:/task/task.yaml:ro"]
    parts += [image, "bash", "-lc", agent_cmd]
    t0 = time.time()
    try:
        r = subprocess.run(" ".join(parts), shell=True, capture_output=True,
                           text=True, timeout=timeout)
        ok, rc = r.returncode == 0, r.returncode
        out, err = r.stdout, r.stderr
        to = False
    except subprocess.TimeoutExpired:
        subprocess.run(f"docker rm -f {container}", shell=True, capture_output=True)
        ok, rc, out, err, to = False, -1, "", f"AGENT_TIMEOUT after {timeout}s", True
    elapsed = round(time.time() - t0, 2)
    if export_workspace_to:
        export_workspace_to.mkdir(parents=True, exist_ok=True)
        c = f"docker create --name {container}-x {image} >/dev/null && " \
            f"docker cp {container}-x:/workspace {export_workspace_to} && " \
            f"docker rm {container}-x >/dev/null"
        subprocess.run(c, shell=True, capture_output=True, timeout=120)
    return SandboxResult(ok=ok, returncode=rc, stdout=out, stderr=err,
                         elapsed=elapsed, timed_out=to, backend="docker")


def run_local(workspace: Path, agent_cmd: str, *, timeout: int = 600,
              mem_limit_mb: int | None = None) -> SandboxResult:
    """本地子进程执行 agent（验证用）：在 workspace 目录内运行。

    mem_limit 在类 Unix 用 resource.setrlimit 软限制（超出即 SIGXFSZ/SIGSEGV）；
    Windows 不支持，退化为仅超时限制。
    """
    t0 = time.time()
    preexec = None
    if mem_limit_mb and hasattr(__import__("resource", fromlist=["x"]), "RLIMIT_AS"):
        import resource
        mb = mem_limit_mb * 1024 * 1024
        def _limit():
            resource.setrlimit(resource.RLIMIT_AS, (mb, mb))
        preexec = _limit
    try:
        r = subprocess.run(agent_cmd, shell=True, cwd=str(workspace),
                          capture_output=True, text=True, timeout=timeout,
                          preexec_fn=preexec)
        ok, rc = r.returncode == 0, r.returncode
        out, err, to = r.stdout, r.stderr, False
    except subprocess.TimeoutExpired:
        ok, rc, out, err, to = False, -1, "", f"AGENT_TIMEOUT after {timeout}s", True
    except MemoryError:
        ok, rc, out, err, to = False, -1, "", "MEM_LIMIT_EXCEEDED", False
    elapsed = round(time.time() - t0, 2)
    return SandboxResult(ok=ok, returncode=rc, stdout=out, stderr=err,
                         elapsed=elapsed, timed_out=to, backend="local")


def cp_solution_to_workspace(solution_dir: Path, workspace: Path) -> None:
    """oracle 模式：把标准解法覆盖进 workspace，用于校验 verifier 本身正确。"""
    import shutil
    workspace.mkdir(parents=True, exist_ok=True)
    for src in solution_dir.rglob("*"):
        if src.is_file():
            dst = workspace / src.relative_to(solution_dir)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)
