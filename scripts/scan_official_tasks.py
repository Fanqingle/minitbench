#!/usr/bin/env python3
"""扫描官方 LHTB 任务集，产出任务画像 + 反作弊设计图谱。

用途：
  1. 选任务——挑出「依赖轻 / 能自建 slim 镜像 / 不依赖 sidecar」的可跑任务
  2. 取证——提取每个任务的 verifier 设计（产物重放、HMAC 链、隐藏 oracle、
     非 root 运行、per-move token 等），形成「官方如何让 verifier 不可作弊」的图谱

产出：results/official/_profile.json + 控制台表格
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 反作弊设计特征——从 README / instruction / task.toml 注释里取证
ANTI_PATTERNS = {
    "artifact_replay": r"replay|replays|replaying|fresh (engine|emulator|pristine)",
    "hmac_chain": r"hmac|authenticated|chained",
    "hidden_oracle": r"hidden (verifier|test|oracle|solution)|withheld|hides all solutions",
    "unprivileged": r"unprivileged|non-root|as a non-root",
    "token_gating": r"per-move token|fresh per-move|token",
    "sidecar_isolated": r"sidecar|isolated container|docker-compose",
    "wallclock_floor": r"wall-clock floor|pacing floor|real wall-clock",
    "band_scoring": r"bands|band_for|milestones",
    "readiness_gate": r"readiness gate|gate\.py",
}


def load_toml(p: Path) -> dict:
    try:
        with p.open("rb") as f:
            return tomllib.load(f)
    except Exception as e:  # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


def count_reqs(dockerfile: str) -> dict:
    """粗数 Dockerfile 的依赖重量——决定能否安全精简自建。"""
    apt = re.findall(r"apt-get install[^\n]*(?:\\\n[^\n]*)*", dockerfile)
    apt_pkgs = set()
    for blk in apt:
        blk = blk.replace("\\\n", " ")
        for tok in blk.split()[2:]:
            if not tok.startswith("-") and tok != "install":
                apt_pkgs.add(tok.strip())
    pip = set(re.findall(r"\b(?:pip3?|python3? -m pip)\s+install\s+([^\n&|]*)", dockerfile))
    pip_pkgs = set()
    for blk in pip:
        for tok in blk.split():
            if not tok.startswith("-"):
                pip_pkgs.add(tok.strip("'\""))
    return {
        "apt_pkgs": sorted(apt_pkgs),
        "pip_pkgs": sorted(pip_pkgs),
        "n_apt": len(apt_pkgs),
        "n_pip": len(pip_pkgs),
        "lines": dockerfile.count("\n") + 1,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench-root", default="~/bench/lhtb",
                    help="官方 LHTB 仓库根目录（含 tasks/）")
    ap.add_argument("--out", default="results/official/_profile.json")
    a = ap.parse_args()
    BENCH = Path(a.bench_root).expanduser()
    OUT = ROOT / a.out

    tdir = BENCH / "tasks"
    if not tdir.is_dir():
        print(f"no tasks dir: {tdir}")
        return 1

    rows = []
    for d in sorted(p for p in tdir.iterdir() if p.is_dir()):
        tt = d / "task.toml"
        if not tt.exists():
            continue
        meta = load_toml(tt)
        task = meta.get("task", {})
        m = meta.get("metadata", {})
        env = meta.get("environment", {})
        ver = meta.get("verifier", {})

        df = d / "environment" / "Dockerfile"
        dockerfile = df.read_text(encoding="utf-8", errors="replace") if df.exists() else ""
        reqs = count_reqs(dockerfile)

        # 证据文本：README + instruction + task.toml 全文
        blob = ""
        for cand in (d / "instruction.md", tt, d / "README.md"):
            if cand.exists():
                blob += cand.read_text(encoding="utf-8", errors="replace")
        blob_l = blob.lower()
        anti = {k: bool(re.search(v, blob_l)) for k, v in ANTI_PATTERNS.items()}

        has_compose = (d / "environment" / "docker-compose.yaml").exists()
        # ★ 离线自建的**事实判据**：harness 随仓库分发（纯 Python 引擎）才可能自建。
        # 早先用 runnable_score 启发式代替，会把「依赖轻但没有 harness」的任务
        # 也算成可自建——那是把猜测写进报告。
        hdir = d / "environment" / "harness"
        harness_files = (sorted(p.name for p in hdir.rglob("*") if p.is_file())
                         if hdir.is_dir() else [])
        rows.append({
            "slug": d.name,
            "id": task.get("name", ""),
            "difficulty": m.get("difficulty", ""),
            "category": m.get("category", ""),
            "expert_min": m.get("expert_time_estimate_min"),
            "keyword_n": len(m.get("tags", []) or []),
            "artifacts": meta.get("artifacts", []),
            "official_image": env.get("docker_image", ""),
            "continue_until_timeout": bool(meta.get("agent", {}).get("continue_until_timeout")),
            "agent_timeout_sec": meta.get("agent", {}).get("timeout_sec"),
            "verifier_timeout_sec": ver.get("timeout_sec"),
            "has_compose": has_compose,
            "has_harness": hdir.is_dir(),
            "harness_n": len(harness_files),
            "harness_files": harness_files[:20],
            "n_apt": reqs["n_apt"],
            "n_pip": reqs["n_pip"],
            "apt_pkgs": reqs["apt_pkgs"][:12],
            "pip_pkgs": reqs["pip_pkgs"][:12],
            "dockerfile_lines": reqs["lines"],
            "anti": anti,
            "n_anti": sum(anti.values()),
            # 可跑性打分：依赖越轻、无 sidecar 越容易精简自建
            "runnable_score": (
                (2 if reqs["n_apt"] <= 3 else 0)
                + (2 if reqs["n_pip"] <= 4 else 0)
                + (2 if not has_compose else 0)
                + (1 if dockerfile else 0)
                + (1 if meta.get("artifacts") else 0)
            ),
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 控制台表 ----
    print(f"scanned {len(rows)} tasks -> {OUT}\n")
    print(f"{'slug':<44}{'diff':<7}{'cat':<20}{'apt':>4}{'pip':>4}"
          f"{'cmp':>5}{'har':>4}{'anti':>6}{'run':>5}")
    print("-" * 100)
    for r in sorted(rows, key=lambda x: -x["runnable_score"]):
        print(f"{r['slug']:<44}{r['difficulty']:<7}{r['category']:<20}"
              f"{r['n_apt']:>4}{r['n_pip']:>4}"
              f"{'Y' if r['has_compose'] else '-':>5}"
              f"{'Y' if r['has_harness'] else '-':>4}"
              f"{r['n_anti']:>6}{r['runnable_score']:>5}")

    # ---- 反作弊特征聚合 ----
    print("\n=== 反作弊设计特征覆盖（46 任务） ===")
    for k in ANTI_PATTERNS:
        hits = [r["slug"] for r in rows if r["anti"][k]]
        print(f"{k:<20} {len(hits):>3}  {', '.join(hits[:4])}"
              f"{' ...' if len(hits) > 4 else ''}")

    nh = [r["slug"] for r in rows if r["has_harness"]]
    print(f"\n=== 离线自建上限：harness 随仓库分发的任务（{len(nh)} 个） ===")
    for r in rows:
        if r["has_harness"]:
            print(f"  {r['slug']:<44} harness_files={r['harness_n']:<4}"
                  f" apt={r['n_apt']} pip={r['n_pip']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
