#!/usr/bin/env python3
"""跑官方 Terminal-Bench / Harbor 任务集（Long-Horizon-Terminal-Bench 等）。

必须在 Linux/WSL 下执行（需要 docker）。

示例：
  python3 scripts/run_official_bench.py \\
      --bench-root ~/bench/lhtb --tasks 2048 \\
      --model qwen3-coder-flash --max-steps 10

产出：
  results/official/<slug>/trajectory.json    agent 逐步轨迹（真实模型 + 真实容器）
  results/official/<slug>/artifacts/<name>   从容器导出的官方声明产物
  results/official/summary.json              本地重建评分结果

为什么是「本地重建评分」：官方 verifier 是隐藏的（服务端），公开仓库里只有
task.toml 声明的 `artifacts`。因此本地只能复现评分**契约**——把产物放回一个
全新引擎重放。对 2048 而言这恰恰也是官方评分逻辑本身（同一 seed → 确定性重放）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mini_tbench.agent_runtime import run_agent      # noqa: E402
from mini_tbench.harbor import (                      # noqa: E402
    HarborTask, build_executor, discover, ensure_image, load_task,
)


# ---------------------------------------------------------------------------
# 本地重建评分（rebuild-from-artifact）
# ---------------------------------------------------------------------------
def _load_official_engine(engine_path: Path):
    spec = importlib.util.spec_from_file_location("official_g2048_engine",
                                                  str(engine_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rebuild_score_2048(task: HarborTask, moves_text: str) -> dict:
    """复现官方 rebuild-from-artifact 评分。

    verifier 不读 agent 自报的分数，而是把 moves.log 放进**全新引擎**逐条重放，
    用重放出的最大方块定 band。PRNG 由烘焙进镜像的 seed 决定，因此重放与实时
    对局 bit-for-bit 一致 —— 日志无法伪造分数，只有真实合并能抬高最大方块。
    """
    engine = task.dir / "environment" / "harness" / "game2048_engine.py"
    if not engine.exists():
        return {"error": f"official engine not found: {engine}"}
    mod = _load_official_engine(engine)
    g = mod.Game2048()
    applied = invalid = 0
    for raw in moves_text.splitlines():
        tok = raw.strip()
        if not tok:
            continue
        if tok == "RESET":
            g.reset()
            applied += 1
            continue
        if tok not in mod.DIRS:
            invalid += 1
            continue
        if g.game_over():
            break
        g.move(tok)
        applied += 1
    mx = int(g.max_tile())
    band = int(mod.band_for(mx))
    return {
        "max_tile": mx,
        "band": band,
        "max_band": len(mod.MILESTONES),
        "moves_applied": applied,
        "invalid_tokens": invalid,
        "score": int(g.score),
        "normalized_reward": round(band / len(mod.MILESTONES), 3),
        "milestones": mod.MILESTONES,
    }


def find_artifact(ex, name: str) -> str:
    """官方只声明产物文件名，实际路径由镜像决定——用 find 兜底定位。"""
    for cand in (f"/app/{name}", f"/opt/g2048/runs/{name}"):
        if ex.run(f"test -e {cand}", timeout=30).returncode == 0:
            return ex.get_text(cand)
    r = ex.run(f"find / -name '{name}' -not -path '/proc/*' "
               f"-not -path '/sys/*' 2>/dev/null | head -1", timeout=60)
    path = r.output.strip().splitlines()[-1] if r.output.strip() else ""
    return ex.get_text(path) if path else ""


# ---------------------------------------------------------------------------
def run_one(task: HarborTask, *, model: str, backend: str, api_key: str | None,
            max_steps: int, temperature: float, out_root: Path,
            force_build: bool = False) -> dict:
    slug = task.slug
    tdir = out_root / slug
    tdir.mkdir(parents=True, exist_ok=True)
    rec: dict = {"slug": slug, "id": task.id, "task_meta": task.summary()}

    print(f"\n=== {slug} | official_image={task.image} | steps<={max_steps} ===",
          flush=True)
    image, src = ensure_image(task, force_build=force_build)
    rec["image"] = image
    rec["image_source"] = src
    ex = build_executor(task, image=image)
    print(f"[container] starting {ex.name} on {image} ({src}) ...", flush=True)
    ex.start()
    rec["container"] = ex.name
    try:
        env_probe = ex.run("pwd && ls -la /app 2>/dev/null | head -5 && "
                           "which python3 && cat /etc/os-release | head -1")
        rec["env_probe"] = env_probe.output[:600]
        print(f"[container] {env_probe.output[:200]}", flush=True)

        run = run_agent(None, task.instruction, backend=backend, model=model,
                        api_key=api_key, max_steps=max_steps,
                        temperature=temperature, executor=ex, workdir="/app")
        rec.update({
            "ok": run.ok, "stop_reason": run.stop_reason,
            "n_steps": len(run.steps), "elapsed": run.elapsed,
            "usage": run.usage_total, "model": run.model,
            "final_answer": run.final_answer[:1500],
        })
        (tdir / "trajectory.json").write_text(
            json.dumps(run.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8")

        print(f"[agent] ok={run.ok} stop={run.stop_reason} "
              f"steps={len(run.steps)} elapsed={run.elapsed}s", flush=True)

        # 产物导出 + 本地重建评分
        (tdir / "artifacts").mkdir(exist_ok=True)
        rec["artifacts"] = {}
        for a in task.artifacts:
            text = find_artifact(ex, a)
            (tdir / "artifacts" / a).write_text(text, encoding="utf-8")
            rec["artifacts"][a] = {"chars": len(text),
                                   "head": text[:200]}
            print(f"[artifact] {a}: {len(text)} chars", flush=True)
            if a.endswith(".log") and "2048" in slug:
                sc = rebuild_score_2048(task, text)
                rec["rebuild_score"] = sc
                print(f"[rebuild-score] {json.dumps(sc, ensure_ascii=False)}",
                      flush=True)
    finally:
        ex.stop()
        print(f"[container] stopped {ex.name}", flush=True)

    (tdir / "record.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench-root", required=True)
    ap.add_argument("--tasks", default="", help="逗号分隔的 slug；空=全部")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--model", default="qwen3-coder-flash")
    ap.add_argument("--backend", default="dashscope")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--max-steps", type=int, default=12)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--force-build", action="store_true",
                    help="跳过官方镜像 pull，直接用官方 Dockerfile 精简自建")
    ap.add_argument("--out-dir", default="results/official")
    a = ap.parse_args()

    tasks = discover(a.bench_root)
    if not tasks:
        print(f"no harbor tasks found under {a.bench_root}")
        return 1
    print(f"discovered {len(tasks)} official tasks")
    if a.tasks:
        want = {t.strip() for t in a.tasks.split(",") if t.strip()}
        tasks = [t for t in tasks if t.slug in want or t.id in want]
    if a.limit:
        tasks = tasks[: a.limit]
    if not tasks:
        print("no task matched selection")
        return 1

    out_root = ROOT / a.out_dir
    out_root.mkdir(parents=True, exist_ok=True)
    records = []
    for t in tasks:
        try:
            records.append(run_one(
                t, model=a.model, backend=a.backend, api_key=a.api_key,
                max_steps=a.max_steps, temperature=a.temperature,
                out_root=out_root, force_build=a.force_build))
        except Exception as e:
            print(f"[FAIL] {t.slug}: {type(e).__name__}: {e}", flush=True)
            records.append({"slug": t.slug, "error": f"{type(e).__name__}: {e}"})

    summary = {
        "generated_at": dt.datetime.now().isoformat(),
        "bench_root": str(a.bench_root),
        "n_tasks_total": len(discover(a.bench_root)),
        "n_run": len(records),
        "model": a.model,
        "max_steps": a.max_steps,
        "records": records,
    }
    (out_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== official bench done: {len(records)} task(s) -> {out_root} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
