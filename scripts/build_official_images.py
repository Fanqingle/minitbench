#!/usr/bin/env python
"""批量预构建「自带 harness」的官方任务精简镜像。

为什么单独成一个脚本
--------------------
`run_official_bench.py` 是**按需**建镜像（`ensure_image`）。但构建是会失败的
（精简规则摘掉了 apt/pip 块，个别任务的引擎自己就依赖某个包），如果在跑批
过程中现场失败，那一次运行就白等了——实测 unknown-config 一条轨迹要 748s。

所以把「构建」和「跑批」拆开：先把所有能建的镜像建好，失败清单当场拿到并
逐个用 `KEEP_PIP` 补齐，再开跑。构建可重复、快；跑批不可重复、慢。

用法
----
    python scripts/build_official_images.py --bench-root ~/bench/lhtb
    python scripts/build_official_images.py --bench-root ~/bench/lhtb \
        --only super-mario,generals-bot-arena
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mini_tbench.harbor import build_local_image, discover  # noqa: E402

# 与 run_official_bench.py 保持一致的引擎侧依赖补回表。
# 这里刻意**重复定义**而不是 import：构建脚本要能在跑批脚本改坏时独立工作，
# 这是「先建后跑」拆分的意义之一。
KEEP_PIP = {
    "2048": [],
    "sokoban": [],
    "snake_maze_campaign": [],
    "unknown-config-semantics": [],
    "spot-scheduler-traces": ["numpy"],
    "grammar-fuzz-coverage-hunt": ["coverage"],
    "tabular-data-feature-covshift": [
        "numpy>=1.26,<3", "pandas>=2.2,<3", "scikit-learn>=1.4,<2",
        "scipy>=1.11,<2", "cloudpickle>=3.0,<4",
    ],
    # vector-db 走 raw 构建，官方 pip 块原样保留，不需要 keep_pip 补
    "vector-db-iterative-build": [],
}

# 构建期就需要、但被精简规则摘掉的 apt 工具。
# 判据：Dockerfile 里在 **构建阶段**（不是运行阶段）调用它——
# poc-exploit-craft 的 `RUN make -C /app/harness/stages/stageN` 就要 gcc/make。
KEEP_APT = {
    "poc-exploit-craft": ["build-essential"],
}

# 精简后语义已不完整的任务：不做精简，按官方 Dockerfile 原样构建。
# vector-db-iterative-build 先 apt 装 python3.11，再 update-alternatives 指向它；
# 摘掉 apt 块后那一步找不到解释器。补 apt 等于在猜官方的依赖闭包，不如照抄。
RAW_SLUGS = {"vector-db-iterative-build"}

# 明确记录"从公开仓库建不出来"的任务及原因——这是结论，不是待办。
NOT_BUILDABLE = {
    "sudoku-recovery": (
        "harness/ 里没有 private/ 目录：oracle + 密钥是 verifier 侧内容，"
        "公开仓库不提供，而 Dockerfile 要求 chmod /opt/sudoku/private。"
        "**这是反作弊设计本身**——非 root agent 读不到的那份东西，本地也拿不到。"
    ),
    "duckdb-optimizer-closure": (
        "构建需要网络（git clone duckdb@pin）+ 源码编译 DuckDB 基线（cmake/ninja，"
        "数十分钟量级）；离线内网复现不现实。"
    ),
}

PIP_INDEX = "https://pypi.org/simple"


def has_local_harness(task) -> bool:
    """任务 harness 随仓库分发（纯 Python 引擎）才可能离线自建。"""
    return (task.dir / "environment" / "harness").is_dir()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench-root", required=True)
    ap.add_argument("--only", default="", help="逗号分隔的 slug；空=全部")
    ap.add_argument("--out", default="results/official/_build_report.json")
    ap.add_argument("--force", action="store_true",
                    help="先删掉同名 slim 镜像再重建（构建逻辑改动后需要）")
    a = ap.parse_args()

    if a.force:
        # 早期版本把 base 强行统一成 python:3.11-slim，已建好的镜像可能并不忠实
        # （super-mario 官方是 3.10、nbody 官方是 ubuntu:22.04）。改完构建逻辑后
        # 需要重建才能让镜像与官方 FROM 一致——直接删掉旧 tag 最省事。
        for t in discover(a.bench_root):
            if has_local_harness(t):
                for suf in ("slim", "raw"):
                    subprocess.run(["docker", "rmi", "-f",
                                    f"lhtb-local-{t.slug}:{suf}"],
                                   capture_output=True, text=True)

    tasks = [t for t in discover(a.bench_root) if has_local_harness(t)]
    if a.only:
        want = {s.strip() for s in a.only.split(",") if s.strip()}
        tasks = [t for t in tasks if t.slug in want]
    print(f"{len(tasks)} task(s) with local harness", flush=True)

    ok: list[str] = []
    fail: list[dict] = []
    skip: list[dict] = []
    for t in tasks:
        if t.slug in NOT_BUILDABLE:
            skip.append({"slug": t.slug, "reason": NOT_BUILDABLE[t.slug]})
            print(f"[SKIP] {t.slug:34s} {NOT_BUILDABLE[t.slug][:80]}", flush=True)
            continue
        t0 = time.time()
        keep = KEEP_PIP.get(t.slug)
        kapt = KEEP_APT.get(t.slug)
        is_raw = t.slug in RAW_SLUGS
        try:
            tag = build_local_image(t, keep_pip=keep, pip_index=PIP_INDEX,
                                    keep_apt=kapt, raw=is_raw)
            ok.append(t.slug)
            print(f"[OK]   {t.slug:34s} -> {tag}  ({time.time() - t0:.0f}s)"
                  f"  mode={'raw' if is_raw else 'slim'}"
                  f" pip={keep or '[]'} apt={kapt or '[]'}", flush=True)
        except Exception as e:  # noqa: BLE001
            fail.append({"slug": t.slug, "error": f"{type(e).__name__}: {e}",
                         "keep_pip": keep, "keep_apt": kapt,
                         "raw": is_raw})
            print(f"[FAIL] {t.slug:34s} {type(e).__name__}: {str(e)[:300]}",
                  flush=True)

    rep = {"n_tasks": len(tasks), "n_ok": len(ok), "n_fail": len(fail),
           "n_skip": len(skip), "ok": ok, "fail": fail, "skip": skip}
    outp = ROOT / a.out
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"\nbuilt {len(ok)}/{len(tasks)} -> {outp}", flush=True)
    if fail:
        print("failed: " + ", ".join(f["slug"] for f in fail), flush=True)
    return 0 if not fail else 2


if __name__ == "__main__":
    raise SystemExit(main())
