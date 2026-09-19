"""容器执行后端：把 agent 的动作放进真实 Docker 沙箱（对齐 Terminal-Bench 形态）。

设计取舍：Terminal-Bench / Harbor 系列的 agent 是**纯终端**形态——环境里只有
shell，没有 read_file / write_file 这类结构化工具。因此容器模式下工具集收敛为
{bash, finish}，一切文件操作经由 shell 完成；这与官方 leaderboard 上 agent 的
真实能力边界一致，避免"用更富的工具集跑出更好看的分数"这种不可比。

需要 Linux/WSL（宿主机需可用 docker）。Windows 侧调用请经由 WSL 执行本模块。
"""
from __future__ import annotations

import subprocess
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class ExecResult:
    returncode: int
    output: str = ""
    timed_out: bool = False


class DockerError(RuntimeError):
    pass


def docker_available() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


@dataclass
class DockerExecutor:
    """一个常驻容器，agent 的所有动作通过 `docker exec` 进入。

    - `network=False` 对齐官方 task.toml 里的 allow_internet=false
    - 资源上限取自 task.toml（cpus / memory_mb），保证与官方评测同构
    """
    image: str
    name: str = ""
    cpus: int = 2
    memory_mb: int = 4096
    network: bool = False
    workdir: str = "/app"
    extra_env: dict[str, str] = field(default_factory=dict)
    _started: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            self.name = f"mtb-{uuid.uuid4().hex[:10]}"

    # -- 生命周期 ---------------------------------------------------------
    def pull(self, timeout: int = 1800) -> None:
        r = subprocess.run(["docker", "pull", self.image],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise DockerError(f"docker pull failed: {r.stderr[-400:]}")

    def image_exists(self) -> bool:
        r = subprocess.run(["docker", "image", "inspect", self.image],
                           capture_output=True)
        return r.returncode == 0

    def start(self, timeout: int = 120) -> None:
        # 只在本地没有该镜像时才 pull。
        # 踩坑记录：早期版本无条件 pull，导致「用官方 Dockerfile 自建的本地镜像」
        # 也会被拿去 Docker Hub 解析，直接 403 失败——本地已有镜像却被判为不可用。
        if not self.image_exists():
            self.pull()
        cmd = ["docker", "run", "-d", "--name", self.name,
               "--cpus", str(self.cpus), "--memory", f"{self.memory_mb}m",
               "-w", self.workdir]
        if not self.network:
            cmd += ["--network", "none"]
        for k, v in self.extra_env.items():
            cmd += ["-e", f"{k}={v}"]
        cmd += [self.image, "sleep", "infinity"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise DockerError(f"docker run failed: {r.stderr[-400:]}")
        self._started = True
        # 等容器真正可 exec
        for _ in range(20):
            if self.run("true", timeout=20).returncode == 0:
                return
            time.sleep(0.5)

    def stop(self) -> None:
        if self._started:
            subprocess.run(["docker", "rm", "-f", self.name],
                           capture_output=True)
            self._started = False

    def __enter__(self) -> "DockerExecutor":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- 执行 -------------------------------------------------------------
    def run(self, cmd: str, timeout: int = 120) -> ExecResult:
        argv = ["docker", "exec", self.name, "bash", "-lc", cmd]
        try:
            r = subprocess.run(argv, capture_output=True, text=True,
                               timeout=timeout, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return ExecResult(-1, f"[executor] TIMEOUT after {timeout}s", True)
        out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
        return ExecResult(r.returncode, out.strip() or "(no output)")

    def put_text(self, path: str, content: str, timeout: int = 60) -> None:
        """把文本写进容器（用 stdin 传，避免转义地狱）。"""
        argv = ["docker", "exec", "-i", self.name, "bash", "-lc",
                f"mkdir -p \"$(dirname {path})\" && cat > {path}"]
        r = subprocess.run(argv, input=content, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise DockerError(f"put_text failed: {r.stderr[-300:]}")

    def get_text(self, path: str, timeout: int = 60) -> str:
        r = subprocess.run(["docker", "exec", self.name, "bash", "-lc",
                            f"cat {path}"], capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return r.stdout or ""

    def exists(self, path: str) -> bool:
        return self.run(f"test -e {path}", timeout=30).returncode == 0
