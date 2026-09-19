"""端到端演示：把 JD 六项职责串成一条可复现的流水线。

  1) 任务构造 / 难度分层            -> tasks/*.yaml（3 个任务：medium/medium/hard）
  2) agent 行为与失败模式            -> oracle / gaming / no-op 三种 agent
  3) 可复现环境 + 超时/资源限制      -> mini_tbench.sandbox
  4) 可执行 verifier + 反作弊        -> mini_tbench.verifier
  5) 轨迹采集 + 数据质量体系         -> rollout / reward / sft / dedup / review
  6) 交付规范（模糊目标->验收标准）  -> docs/ + benchmark 污染扫描

运行：
  python scripts/demo_pipeline.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mini_tbench import dedup_jsonl, ContaminationScanner, GamingAnalyzer  # noqa: E402
from mini_tbench.review import ReviewQueue  # noqa: E402
from mini_tbench.rollout import do_rollout  # noqa: E402
from mini_tbench.sft import export_from_jsonl  # noqa: E402
from mini_tbench.task_spec import load_task  # noqa: E402

DEMO = ROOT / "results" / "demo"


# ---------- 三种 agent 行为（可复现，不依赖外部 CLI / GPU） ----------

def oracle_agent(task_id: str):
    """正确解法由 do_rollout(mode='oracle') 处理，这里仅占位。"""
    return None


def gaming_agent(ws: Path):
    """作弊 agent：直接按 SKU 特判返回期望值 + 恒真断言，试图绕过 verifier。"""
    (ws / "app.py").write_text(
        'class InventoryService:\n'
        '    def __init__(self, stock=None):\n'
        '        self.stock = stock or {}\n'
        '        self.deducted_orders = set()\n'
        '    def deduct(self, sku, qty, order_id):\n'
        '        if sku == "SKU-001": return 10   # hardcode 期望值\n'
        '        return True\n'
        '    def deduct_pieces(self, sku, pieces, order_id):\n'
        '        assert True\n'
        '        return True\n',
        encoding="utf-8")


def noop_agent(ws: Path):
    """不作为 agent：不修改任何文件（保留初始 bug）。"""
    return None


def main() -> int:
    if DEMO.exists():
        shutil.rmtree(DEMO)
    DEMO.mkdir(parents=True)
    results = DEMO / "results.jsonl"
    trajs = []

    def record(t, extra_note=""):
        trajs.append(t)
        with open(results, "a", encoding="utf-8") as f:
            f.write(t.to_jsonl() + "\n")
        print(f"  - {t.run_id:<34} verdict={t.verdict:<4} tag={t.failure_tag:<12} "
              f"tests={t.tests_passed}/{t.tests_total} {extra_note}")

    tasks = {
        "task-01-fix-failing-tests": ("tasks/task-01-fix-failing-tests", "medium"),
        "task-02-csv-pipeline": ("tasks/task-02-csv-pipeline", "medium"),
        "task-03-flaky-cli": ("tasks/task-03-flaky-cli", "hard"),
    }

    print("=" * 78)
    print("[1/6] Oracle 校验（verifier 本身必须先全绿，否则一切评测不可信）")
    print("=" * 78)
    for tid, (tpath, _diff) in tasks.items():
        task = load_task(ROOT / tpath)
        record(do_rollout(task, f"{tid}-oracle", mode="oracle", results_dir=DEMO))

    print()
    print("=" * 78)
    print("[2/6] Agent 行为采样（oracle / gaming / no-op）")
    print("=" * 78)
    t1 = load_task(ROOT / tasks["task-01-fix-failing-tests"][0])
    record(do_rollout(t1, "task-01-gaming", results_dir=DEMO, agent_fn=gaming_agent),
           "<- 预期 gaming")
    for tid in ("task-02-csv-pipeline", "task-03-flaky-cli"):
        task = load_task(ROOT / tasks[tid][0])
        record(do_rollout(task, f"{tid}-noop", results_dir=DEMO, agent_fn=noop_agent),
               "<- 预期 logic_error")

    print()
    print("=" * 78)
    print("[3/6] 奖励设计（RLVR scalar reward）")
    print("=" * 78)
    from mini_tbench.reward import compute_reward
    for t in trajs:
        r = compute_reward(t.verdict, t.tests_passed, t.tests_total,
                           t.timed_out, t.failure_tag, bool(t.diff))
        print(f"  - {t.run_id:<34} reward={r.total:+.3f} {r.breakdown}")

    print()
    print("=" * 78)
    print("[4/6] 数据质量体系：去重 / 人工抽检 / SFT·RLVR 导出")
    print("=" * 78)
    dd = dedup_jsonl(results, threshold=0.9)
    print(f"  - 去重：kept={dd['kept']} removed={dd['removed']}")
    n = ReviewQueue(results).export_csv(DEMO / "review_queue.csv")
    print(f"  - 人工抽检待办：{n} 条 -> {DEMO / 'review_queue.csv'}")
    stat = export_from_jsonl(results, DEMO / "train")
    print(f"  - 训练数据：SFT={stat['sft']} 条，RLVR={stat['rlvr']} 条")

    print()
    print("=" * 78)
    print("[5/6] 作弊分析（gaming）与污染扫描（contamination）")
    print("=" * 78)
    ga = GamingAnalyzer()
    for t in trajs:
        rep = ga.analyze(t)
        if rep.flagged:
            print(f"  - {t.run_id}: risk={rep.risk} signals={rep.signals} "
                  f"drop={rep.should_drop}")
    scanner = ContaminationScanner()
    for tid, (tpath, _d) in tasks.items():
        c = scanner.scan_task(load_task(ROOT / tpath))
        print(f"  - 污染扫描 {tid}: {c.verdict}（{c.note}）")

    print()
    print("=" * 78)
    print("[6/6] 汇总统计")
    print("=" * 78)
    total = len(trajs)
    passed = sum(1 for t in trajs if t.verdict == "PASS")
    gaming = sum(1 for t in trajs if t.failure_tag == "gaming")
    print(json.dumps({
        "总计": total, "通过": passed, "失败": total - passed,
        "gaming 失败": gaming,
        "通过率": round(passed / total, 3),
        "产物": str(DEMO),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
