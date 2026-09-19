"""harbor 精简逻辑的回归测试。

为什么单独成篇：这几个纯函数是本轮**最贵的 bug 来源**——它们每一个失效，
表现都不是"测试红了"，而是"某次跑批白等十几分钟"或"某条记录凭空消失"。
纯函数、无副作用、可完全离线断言，是最该被钉死的一层。

覆盖四个人工踩过的坑：
  1. 按物理行切 Dockerfile → 一条多行 RUN 被从中间劈开（sokoban 构建失败）
  2. 构建期就要用的包被摘掉，补回的包却追加在文件末尾（顺序错）
  3. base 被强行统一 → `ubuntu:22.04` 类任务里面没有 pip，补包命令直接失败
  4. `FROM --platform=...` 形态没被识别 → 插入位置错乱
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mini_tbench.harbor import (  # noqa: E402
    _inject_after_from, _logical_blocks, slim_dockerfile,
)


# ---------------------------------------------------------------------------
# 1. 逻辑行分组
# ---------------------------------------------------------------------------
def test_logical_blocks_keeps_multiline_run_as_one_block():
    """一条带续行的 RUN 必须留在同一个块里——按物理行切会劈开它。"""
    text = (
        "FROM python:3.11-slim\n"
        "RUN set -eux; \\\n"
        "    for f in a b c; do \\\n"
        "      printf 'x' > \"$f\"; \\\n"
        "    done\n"
        "ENV A=1\n"
    )
    blocks = _logical_blocks(text.splitlines())
    assert len(blocks) == 3, [len(b) for b in blocks]
    # 中间那块就是那条完整的 RUN，必须含结尾的 done（而不是被截断）
    assert blocks[1][0].strip().startswith("RUN")
    assert "done" in blocks[1][-1]
    assert len(blocks[1]) == 4


def test_slim_dockerfile_never_splits_a_run():
    """精简后若出现 `; do RUN ...` 这类拼接，说明 RUN 被劈开了。"""
    text = (
        "FROM python:3.11-slim\n"
        "RUN set -eux; \\\n"
        "    for f in a b c; do \\\n"
        "      printf 'x' > \"$f\"; \\\n"
        "    done\n"
    )
    out = slim_dockerfile(text)
    assert "; do RUN" not in out
    assert "RUN printf" not in out
    assert "done" in out


# ---------------------------------------------------------------------------
# 2. 只摘 apt/pip，其余逐字保留
# ---------------------------------------------------------------------------
def test_slim_dockerfile_drops_apt_and_pip_keeps_contract():
    text = (
        "FROM python:3.11-slim\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends \\\n"
        "    tmux asciinema \\\n"
        "    && rm -rf /var/lib/apt/lists/*\n"
        "RUN pip install --no-cache-dir pytest==8.4.1\n"
        "COPY harness/ /app/harness/\n"
        "ENV SOKOBAN_RUN_DIR=/opt/sokoban/runs\n"
        "WORKDIR /app\n"
    )
    out = slim_dockerfile(text)
    assert "apt-get" not in out
    assert "pip install" not in out
    # 核心契约必须逐字保留
    assert "COPY harness/ /app/harness/" in out
    assert "ENV SOKOBAN_RUN_DIR=/opt/sokoban/runs" in out
    assert "WORKDIR /app" in out
    assert out.startswith("FROM python:3.11-slim")


def test_slim_dockerfile_drops_hijacked_domain_fetch():
    """构建期访问 raw.githubusercontent.com 在本机会挂死，必须整块摘掉。"""
    text = (
        "FROM python:3.11-slim\n"
        "RUN curl -fsSL https://raw.githubusercontent.com/x/y/main/z.sh "
        "| bash || true\n"
        "COPY workspace/ /app/workspace/\n"
    )
    out = slim_dockerfile(text)
    assert "raw.githubusercontent.com" not in out
    assert "COPY workspace/ /app/workspace/" in out


# ---------------------------------------------------------------------------
# 3. 补回的依赖必须插在 FROM 之后、所有 RUN 之前
# ---------------------------------------------------------------------------
def _first_run_index(lines: list[str], *, skip: str | None = None) -> int:
    """第一条 RUN 的下标；skip 用于排除注入行自身（它也以 RUN 开头）。"""
    for i, ln in enumerate(lines):
        if skip is not None and ln == skip:
            continue
        if ln.strip().startswith("RUN "):
            return i
    return len(lines)


def test_inject_after_from_lands_before_every_run():
    """顺序错了会以「构建失败」的形式出现，看不出是顺序问题——必须钉死。"""
    text = ("FROM python:3.11-slim\n"
            "COPY harness/ /app/harness/\n"
            "RUN python3 /app/harness/trace_generator.py\n"
            "COPY workspace/ /app/workspace/\n")
    injected = "RUN pip install --no-cache-dir \"numpy\""
    out = _inject_after_from(text, [injected])
    lines = out.splitlines()
    assert lines[0].strip() == "FROM python:3.11-slim"
    assert injected in lines
    # 关键断言：注入行出现在**原文件的第一条 RUN** 之前
    assert lines.index(injected) < _first_run_index(lines, skip=injected), out


def test_inject_after_from_handles_platform_from():
    """`FROM --platform=linux/amd64 x` 也算 FROM——否则会插错位。"""
    text = ("FROM --platform=linux/amd64 python:3.10-slim\n"
            "COPY harness/ /app/harness/\n"
            "RUN echo hi\n")
    injected = "RUN true"
    out = _inject_after_from(text, [injected])
    lines = out.splitlines()
    assert lines[0].strip().startswith("FROM --platform=linux/amd64")
    assert lines.index(injected) == 1
    assert lines.index(injected) < _first_run_index(lines, skip=injected)


def test_inject_after_from_is_noop_without_blocks():
    text = "FROM python:3.11-slim\nRUN echo hi\n"
    assert _inject_after_from(text, []) == text


def test_inject_after_from_falls_back_when_no_from():
    """没有 FROM 时不能丢内容——头部注入即可，别静默吞掉。"""
    out = _inject_after_from("RUN echo hi\n", ["RUN true"])
    assert out.startswith("RUN true")
    assert "RUN echo hi" in out


def test_kept_pip_block_bootstraps_missing_pip():
    """`ubuntu:22.04` 这类 base 里没有 pip，补包命令必须自带引导。

    直接 `pip install` 会以 "pip: not found" 失败，而报错只显示 pip 命令行，
    看不出根因是基础镜像而不是包名。
    """
    from mini_tbench.harbor import HarborTask, build_local_image  # noqa: F401

    import inspect
    src = inspect.getsource(build_local_image)
    assert "command -v pip" in src, "补包块缺少 pip 引导"
    assert "python3-pip" in src, "补包块没有兜底安装 python3-pip"
