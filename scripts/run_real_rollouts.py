#!/usr/bin/env python
"""真实 agent rollout 批次 + step 级归因报告（跨模型对比）。

用法：
  python scripts/run_real_rollouts.py --models qwen3-coder-flash,qwen3-coder-plus --runs 5

产出：
  results/real/results.jsonl   逐条轨迹（含 step 级归因；完整轨迹在 results/real/<run_id>/trajectory.json）
  results/real/report.md       失败归因报告（总体 / 按模型 / 按任务 / root-cause step 分布）
  results/real/summary.json    机器可读聚合
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mini_tbench.rollout import do_rollout          # noqa: E402
from mini_tbench.step_trace import (                # noqa: E402
    CATEGORY_CN, CATEGORY_ORDER, aggregate, render_markdown,
)
from mini_tbench.task_spec import load_task         # noqa: E402


def discover_tasks() -> list[Path]:
    return sorted(p for p in (ROOT / "tasks").iterdir()
                  if (p / "task.yaml").exists())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="qwen3-coder-flash")
    ap.add_argument("--runs", type=int, default=5, help="每个任务×模型采样条数")
    ap.add_argument("--tasks", default="all")
    ap.add_argument("--backend", default="dashscope")
    ap.add_argument("--max-steps", type=int, default=18)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--out-dir", default="results/real")
    a = ap.parse_args()

    out_dir = ROOT / a.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "results.jsonl"

    tasks = discover_tasks()
    if a.tasks != "all":
        want = {t.strip() for t in a.tasks.split(",")}
        tasks = [t for t in tasks if t.name in want]
    models = [m.strip() for m in a.models.split(",") if m.strip()]

    stamp = dt.datetime.now().strftime("%m%d-%H%M%S")
    records: list[dict] = []
    t0 = time.time()
    with open(jsonl, "a", encoding="utf-8") as f:
        for task_dir in tasks:
            task = load_task(task_dir)
            for model in models:
                tag = model.replace("qwen3-", "").replace("-", "")
                print(f"\n=== {task.id} × {model} × {a.runs} ===", flush=True)
                for i in range(a.runs):
                    rid = f"{task.id}__{tag}__{stamp}-{i:02d}"
                    try:
                        tr = do_rollout(
                            task, rid, mode="react", results_dir=out_dir,
                            backend=a.backend, model=model,
                            max_steps=a.max_steps, temperature=a.temperature,
                            timeout=a.timeout)
                    except Exception as e:                 # 单条失败不中断批次
                        print(f"  [{rid}] EXCEPTION {type(e).__name__}: {e}", flush=True)
                        continue
                    rec = json.loads(tr.to_jsonl())
                    rec["_model"] = model
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    records.append(rec)
                    att = rec.get("attribution") or {}
                    print(f"  [{i:02d}] {rec['verdict']:4s} cat={rec['failure_tag']:18s} "
                          f"steps={rec['n_steps']:2d} root@{att.get('root_cause_step')} "
                          f"({att.get('root_cause_tool') or '-'}) "
                          f"t={rec['agent_elapsed']}s tok={rec['usage'].get('total_tokens', 0)}",
                          flush=True)

    if not records:
        print("no records produced")
        return 1

    agg_all = aggregate(records)
    by_model: dict[str, dict] = {}
    for m in models:
        sub = [r for r in records if r["_model"] == m]
        if sub:
            by_model[m] = aggregate(sub)

    summary = {
        "generated_at": dt.datetime.now().isoformat(),
        "n_runs": len(records),
        "wall_clock_s": round(time.time() - t0, 1),
        "max_steps": a.max_steps,
        "temperature": a.temperature,
        "overall": agg_all,
        "by_model": by_model,
        "tokens_total": sum(r["usage"].get("total_tokens", 0) for r in records),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md = ["# 真实 agent 轨迹：规模 rollout 与 step 级失败归因", "",
          f"- 生成时间：{summary['generated_at']}",
          f"- 轨迹总条数：**{summary['n_runs']}**",
          f"- 模型：{', '.join(models)}（backend={a.backend}，temperature={a.temperature}，"
          f"max_steps={a.max_steps}）",
          f"- 总耗时：{summary['wall_clock_s']}s；累计 token：{summary['tokens_total']:,}",
          "", "---", ""]
    md.append(render_markdown(agg_all, records, title="总体失败归因"))
    for m, agg in by_model.items():
        md += ["", "---", "", f"# 模型：{m}", ""]
        md.append(render_markdown(agg, None, title=f"{m} 失败归因"))

    # 跨模型对比
    if len(by_model) > 1:
        md += ["", "---", "", "# 跨模型对比", "",
               "| 模型 | 条数 | 通过率 | 平均步数 | 主导失败类别 |", "|---|---:|---:|---:|---|"]
        for m, agg in by_model.items():
            top = next(iter(agg["category_dist"]), "-")
            md.append(f"| {m} | {agg['n']} | {agg['pass_rate']:.0%} | "
                      f"{agg['avg_steps']} | {top}（{CATEGORY_CN.get(top, top)}） |")
        md += ["", "> 同一批任务、同一 harness、同一温度，仅换模型 → 控制变量的能力边界对比。"]
    (out_dir / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"\n=== DONE: {len(records)} runs in {summary['wall_clock_s']}s "
          f"| pass_rate={agg_all['pass_rate']:.1%} ===")
    print(f"report -> {out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
