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
import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mini_tbench.agent_runtime import run_agent      # noqa: E402
from mini_tbench.harbor import (                      # noqa: E402
    HarborTask, build_executor, discover, ensure_image, load_task,
)
from mini_tbench.official_score import (              # noqa: E402
    has_local_rebuild, rebuild_score,
)

# 各任务被精简掉、但**引擎自身**需要的 pip 包（verifier 侧的渲染依赖不算）。
# 不补回去，容器里 agent 一跑引擎就 ImportError。
KEEP_PIP = {
    "sokoban": [],
    "2048": [],
    "snake_maze_campaign": [],
    "unknown-config-semantics": [],
    "spot-scheduler-traces": ["numpy"],
    "grammar-fuzz-coverage-hunt": ["coverage"],
    "tabular-data-feature-covshift": [
        "numpy>=1.26,<3", "pandas>=2.2,<3", "scikit-learn>=1.4,<2",
        "scipy>=1.11,<2", "cloudpickle>=3.0,<4",
    ],
    "vector-db-iterative-build": [],
}

# 构建期就要用的 apt 工具（被精简规则摘掉后必须补回）
KEEP_APT = {
    "poc-exploit-craft": ["build-essential"],
}

# 精简后语义已不完整的任务 → 按官方 Dockerfile 原样构建（见 harbor.build_local_image）
RAW_SLUGS = {"vector-db-iterative-build"}

PIP_INDEX = os.environ.get("MTB_PIP_INDEX", "https://pypi.org/simple")


# ---------------------------------------------------------------------------
def find_artifact(ex, name: str) -> tuple[bool, str]:
    """官方声明里的产物名**可能是容器内绝对路径**，不是文件名。

    实测：2048 声明 `moves.log`（相对名），而 snake_maze_campaign /
    unknown-config-semantics 声明 `/opt/gsnake/runs/moves.log`（绝对路径）。
    两种形态都要能处理，所以先试「绝对路径照用 + 常见前缀拼接」，再用
    `find` 兜底。

    返回 **(found, text)**，其中 found 是「文件存在」而不是「内容非空」——
    这两件事对评测的意义完全不同：
      - 不存在 = agent 根本没推进到交卷那一步（最靠前的失败层）
      - 存在但为空 = 交卷了但内容是空的（工具调用成功、语义没落地）

    **产物缺失要如实返回，不能抛异常**：异常会把失败信号变成噪音——同一个
    文件路径，既可能是"容器里真没有"，也可能是"本地落盘时把绝对路径当相对
    路径写了"，两者必须能分辨。
    """
    cands = [f"/app/{name}", name if name.startswith("/") else "",
             f"/opt/g2048/runs/{name}", f"/opt/sokoban/runs/{name}",
             f"/opt/gsnake/runs/{name}"]
    for cand in [c for c in cands if c]:
        try:
            if ex.run(f"test -e {cand}", timeout=30).returncode == 0:
                return True, ex.get_text(cand)
        except Exception:  # noqa: BLE001
            continue
    try:
        r = ex.run(f"find / -name '{Path(name).name}' -not -path '/proc/*' "
                   f"-not -path '/sys/*' 2>/dev/null | head -1", timeout=60)
        path = r.output.strip().splitlines()[-1] if r.output.strip() else ""
        return (True, ex.get_text(path)) if path else (False, "")
    except Exception:  # noqa: BLE001
        return False, ""


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
    image, src = ensure_image(task, force_build=force_build,
                              keep_pip=KEEP_PIP.get(slug),
                              pip_index=PIP_INDEX,
                              keep_apt=KEEP_APT.get(slug),
                              raw=slug in RAW_SLUGS)
    if not has_local_rebuild(slug):
        print(f"[scoring] {slug}: verifier is hidden (server-side); "
              f"local run validates the channel + captures artifacts only",
              flush=True)
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
        if not task.artifacts:
            rec["scoring"] = "no declared artifact — server-side verifier only"
        for a in task.artifacts:
            found, text = find_artifact(ex, a)
            # 产物名可能是**容器内绝对路径**（如 "/opt/gsnake/runs/moves.log"）。
            # Path(base) / "/abs/path" 在 Python 里会丢弃 base（绝对路径优先），
            # 于是本地会去写 /opt/gsnake/runs/ 这个不存在的目录并抛
            # FileNotFoundError。落盘时必须只用 basename，原始名仅作记录键。
            local_name = Path(a).name or "artifact"
            (tdir / "artifacts" / local_name).write_text(text, encoding="utf-8")
            absent = not found
            empty = found and not text.strip()
            rec["artifacts"][a] = {
                "chars": len(text), "head": text[:200],
                "local_path": f"artifacts/{local_name}",
                "absent": absent, "empty": empty,
                # missing 保留为「不可用于评分」的汇总口径（不存在或空）
                **({"missing": True} if (absent or empty) else {}),
            }
            if absent:
                print(f"[artifact] {a}: ABSENT — agent 未产出声明产物"
                      f"（真实的失败信号，不是脚本错误）", flush=True)
            elif empty:
                print(f"[artifact] {a}: EMPTY — 产物存在但内容为空"
                      f"（工具调用成功、语义没落地）", flush=True)
            else:
                print(f"[artifact] {a}: {len(text)} chars", flush=True)
            sc = rebuild_score(task, a, text)
            if sc is None:
                rec.setdefault("rebuild_score", None)
                rec["scoring"] = "server-side (verifier is hidden)"
                print("[rebuild-score] hidden verifier — local replay not "
                      "defined for this task; artifact captured only", flush=True)
            else:
                rec["rebuild_score"] = sc
                rec["scoring"] = "local rebuild-from-artifact"
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
            # 打印完整 traceback：只报 `类型: 消息` 时，像「产物名是绝对路径
            # 导致本地写盘写到 /opt/... 」这类 bug 会被误读成"容器里没有产物"，
            # 排查成本从 1 次运行涨到 3 次。harness 的报错必须自己可诊断。
            print(f"[FAIL] {t.slug}: {type(e).__name__}: {e}", flush=True)
            print(traceback.format_exc(), flush=True)
            records.append({"slug": t.slug, "error": f"{type(e).__name__}: {e}"})

    summary = {
        "generated_at": dt.datetime.now().isoformat(),
        "bench_root": str(a.bench_root),
        "n_tasks_total": len(discover(a.bench_root)),
        "n_run": len(records),
        "model": a.model,
        "max_steps": a.max_steps,
        "agg": _aggregate(records),
        "records": records,
    }
    (out_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== official bench done: {len(records)} task(s) -> {out_root} ===")
    print(json.dumps(summary["agg"], ensure_ascii=False, indent=2))
    return 0


def _aggregate(records: list[dict]) -> dict:
    ok = [r for r in records if r.get("ok")]
    ran = [r for r in records if r.get("n_steps")]
    steps = [r["n_steps"] for r in ran]
    toks = [int((r.get("usage") or {}).get("total_tokens", 0)) for r in ran]
    scored = [r for r in records if isinstance(r.get("rebuild_score"), dict)
              and "error" not in r["rebuild_score"]]
    hidden = [r for r in records
              if r.get("scoring", "").startswith("server-side")]
    stops: dict[str, int] = {}
    for r in ran:
        k = r.get("stop_reason") or "?"
        stops[k] = stops.get(k, 0) + 1
    # 产物契约层的聚合：把「不存在」和「存在但空」分开计数。
    # 两者都是"不可用于评分"，但归因完全不同——不存在=没推进到交卷，
    # 空=交卷了但内容没落地。混在一起会看不出该修 prompt 还是修工具。
    n_art_absent = n_art_empty = n_art_ok = 0
    for r in records:
        for v in (r.get("artifacts") or {}).values():
            if not isinstance(v, dict):
                continue
            if v.get("absent"):
                n_art_absent += 1
            elif v.get("empty"):
                n_art_empty += 1
            else:
                n_art_ok += 1
    return {
        "n_run": len(records),
        "n_agent_finished_clean": len(ok),
        "n_errors": sum(1 for r in records if r.get("error")),
        "avg_steps": round(sum(steps) / len(steps), 1) if steps else 0,
        "max_steps_seen": max(steps) if steps else 0,
        "total_tokens": sum(toks),
        "n_local_rebuild_scored": len(scored),
        "n_hidden_verifier": len(hidden),
        "n_artifact_usable": n_art_ok,
        "n_artifact_absent": n_art_absent,
        "n_artifact_empty": n_art_empty,
        "stop_reasons": stops,
        "local_rebuild": {
            r["slug"]: {
                "metric": r["rebuild_score"].get("metric"),
                "value": r["rebuild_score"].get("value"),
                "band": r["rebuild_score"].get("band"),
                "normalized_reward": r["rebuild_score"].get("normalized_reward"),
            }
            for r in scored
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
