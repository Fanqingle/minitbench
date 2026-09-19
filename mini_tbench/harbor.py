"""Harbor / Terminal-Bench 2.0 官方任务接入。

对齐 Long-Horizon-Terminal-Bench（46 task）的官方目录约定：
    tasks/<id>/{task.toml, instruction.md, environment/{Dockerfile,harness/}}

官方评分体系里三个关键设计，也是评测方护城河的同构体现——接入时必须尊重，
否则跑出的分数与官方 leaderboard 不可比：

1. **产物重建评分（rebuild-from-artifact）**：task.toml 顶层声明 `artifacts`，
   verifier 只认产物本身，agent 自报进度一律不算分。
2. **隐藏 verifier**：公开仓库里没有 tests/，评分逻辑在服务端；agent 无法针对
   评分器做定向优化（也无法反向推出评分细节来钻空子）。
3. **确定性重放**：以 2048 任务为例，splitmix64 PRNG 的 seed 被烘焙进镜像
   （G2048_SEED），因此棋盘序列是 (seed, 移动序列) 的纯函数，verifier 用逐字
   复制同一份 engine 重放 move log 即可 bit-for-bit 复现 —— 「只有真实合并
   才能抬高最大方块」，日志无法伪造分数。
"""
from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class HarborTask:
    id: str
    name: str = ""
    description: str = ""
    instruction: str = ""
    image: str = ""
    cpus: int = 2
    memory_mb: int = 4096
    storage_mb: int = 10240
    agent_timeout_sec: int = 3600
    verifier_timeout_sec: int = 1800
    build_timeout_sec: int = 1800
    allow_internet: bool = False
    artifacts: list[str] = field(default_factory=list)
    difficulty: str = ""
    category: str = ""
    keywords: list[str] = field(default_factory=list)
    expert_time_estimate_min: float = 0.0
    junior_time_estimate_min: float = 0.0
    dir: Path | None = None

    @property
    def slug(self) -> str:
        return self.dir.name if self.dir else self.id

    def summary(self) -> dict:
        return {
            "id": self.id, "slug": self.slug, "image": self.image,
            "cpus": self.cpus, "memory_mb": self.memory_mb,
            "agent_timeout_sec": self.agent_timeout_sec,
            "allow_internet": self.allow_internet,
            "artifacts": self.artifacts, "difficulty": self.difficulty,
            "category": self.category, "keywords": self.keywords,
            "expert_time_estimate_min": self.expert_time_estimate_min,
        }


def load_task(task_dir: str | Path) -> HarborTask:
    d = Path(task_dir).resolve()
    toml_path = d / "task.toml"
    if not toml_path.exists():
        raise FileNotFoundError(toml_path)
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    t = data.get("task", {}) or {}
    env = data.get("environment", {}) or {}
    ver = data.get("verifier", {}) or {}
    ag = data.get("agent", {}) or {}
    meta = data.get("metadata", {}) or {}
    instr = d / "instruction.md"
    return HarborTask(
        id=str(t.get("name") or d.name),
        name=str(t.get("name") or d.name),
        description=str(t.get("description") or "").strip(),
        instruction=instr.read_text(encoding="utf-8") if instr.exists() else "",
        image=str(env.get("docker_image") or ""),
        cpus=int(env.get("cpus") or 2),
        memory_mb=int(env.get("memory_mb") or 4096),
        storage_mb=int(env.get("storage_mb") or 10240),
        agent_timeout_sec=int(ag.get("timeout_sec") or 3600),
        verifier_timeout_sec=int(ver.get("timeout_sec") or 1800),
        build_timeout_sec=int(env.get("build_timeout_sec") or 1800),
        allow_internet=bool(env.get("allow_internet", False)),
        artifacts=list(data.get("artifacts") or []),
        difficulty=str(meta.get("difficulty") or ""),
        category=str(meta.get("category") or ""),
        keywords=list(meta.get("tags") or t.get("keywords") or []),
        expert_time_estimate_min=float(meta.get("expert_time_estimate_min") or 0),
        junior_time_estimate_min=float(meta.get("junior_time_estimate_min") or 0),
        dir=d,
    )


def discover(root: str | Path) -> list[HarborTask]:
    """扫描 benchmark 根目录下的 tasks/<id>/task.toml。"""
    root = Path(root)
    tasks_dir = root / "tasks" if (root / "tasks").is_dir() else root
    out: list[HarborTask] = []
    for d in sorted(tasks_dir.iterdir()):
        if (d / "task.toml").exists():
            try:
                out.append(load_task(d))
            except Exception:
                continue
    return out


def build_executor(task: HarborTask, **overrides):
    """按官方 task.toml 的资源约束构造同构沙箱。"""
    from .container import DockerExecutor

    kw = dict(
        image=task.image,
        cpus=task.cpus,
        memory_mb=task.memory_mb,
        network=task.allow_internet,
    )
    kw.update(overrides)
    return DockerExecutor(**kw)


# ---------------------------------------------------------------------------
# 镜像可用性兜底：官方预构建镜像不可达时，用官方 Dockerfile 精简自建
# ---------------------------------------------------------------------------
def _skip_run_block(line: str, text_lines: list[str], i: int) -> tuple[bool, int]:
    """判断第 i 行是否是「需要整体跳过的 RUN 块」，返回 (是否跳过, 下一行下标)。"""
    s = line.strip()
    if not s.upper().startswith(("RUN APT-GET", "RUN APT ", "RUN PIP ", "RUN PIP3 ")):
        return False, i
    j = i
    while text_lines[j].rstrip().endswith("\\") and j + 1 < len(text_lines):
        j += 1
    return True, j


def slim_dockerfile(official_text: str) -> str:
    """从官方 Dockerfile 生成精简版：只摘掉 apt/pip 安装块，其余逐字保留。

    目的：官方预构建镜像在国内加速器上可能 403（不可达），而官方 Dockerfile
    里的 apt-get / pip install 又依赖外网。任务的核心契约——引擎副本、
    launcher 脚本、G2048_SEED / G2048_RUN_DIR 等 ENV——全部保留，
    因此产物重放的确定性不受影响；被摘掉的 tmux / asciinema / opencv
    只是官方 agent harness 用于录制与渲染视频的依赖，与任务语义无关。
    """
    lines = official_text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.strip().startswith("#"):
            i += 1
            continue
        skip, nj = _skip_run_block(raw, lines, i)
        if skip:
            i = nj + 1
            continue
        out.append(raw)
        i += 1
    return "\n".join(out).rstrip() + "\n"


def build_local_image(task: HarborTask, *, base: str = "python:3.11-slim",
                      tag: str | None = None, timeout: int = 1200) -> str:
    """用官方 Dockerfile 的精简版在本地构建镜像，返回 tag。"""
    ctx = task.dir / "environment"
    df = ctx / "Dockerfile"
    if not df.exists():
        raise FileNotFoundError(df)
    slim = slim_dockerfile(df.read_text(encoding="utf-8"))
    # 把基础镜像替换为已确认可用的官方镜像
    slim = "\n".join(
        (f"FROM {base}" if ln.strip().upper().startswith("FROM ") else ln)
        for ln in slim.splitlines()) + "\n"
    tmp_df = ctx / ".slim.Dockerfile"
    tmp_df.write_text(slim, encoding="utf-8")
    tag = tag or f"lhtb-local-{task.slug}:slim"
    r = subprocess.run(["docker", "build", "-f", tmp_df.name, "-t", tag, "."],
                       cwd=str(ctx), capture_output=True, text=True,
                       timeout=timeout, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"local build failed: {(r.stderr or '')[-500:]}")
    return tag


def ensure_image(task: HarborTask, *, force_build: bool = False,
                 base: str = "python:3.11-slim", log=print) -> tuple[str, str]:
    """确保有可用镜像。返回 (image, source)，source ∈ {official, local-slim}。"""
    if not force_build:
        r = subprocess.run(["docker", "pull", task.image], capture_output=True,
                           text=True, timeout=1800, encoding="utf-8",
                           errors="replace")
        if r.returncode == 0:
            log(f"[image] pulled official {task.image}")
            return task.image, "official"
        tail = (r.stderr or "").strip().splitlines()[-1:] or [""]
        log(f"[image] official pull failed: {tail[0][:180]}")
    else:
        log("[image] force_build=True, skipping official pull")
    log(f"[image] building slim image from official Dockerfile "
        f"({task.dir / 'environment' / 'Dockerfile'})")
    tag = build_local_image(task, base=base)
    log(f"[image] built local slim image {tag}")
    return tag, "local-slim"
