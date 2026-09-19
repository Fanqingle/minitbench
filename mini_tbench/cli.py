"""命令行入口：把全部模块串成可演示的端到端流水线。

子命令：
  oracle      用 solution 跑 verifier，确认 verifier 本身正确（每个任务必过）
  rollout     批量 rollout（agent 模式需提供 --agent-cmd 或环境变量 AGENT_CMD）
  export      从 results.jsonl 导出 train_sft.jsonl / train_rlvr.jsonl
  dedup       去重，输出 results.dedup.jsonl
  review      导出人工抽检清单（CSV）
  analyze     反作弊 + 污染 + 奖励统计（数据质量总览）
  scan        扫描任务目录污染指纹
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 直接从子模块导入，避免绕 package 命名空间产生循环导入
from . import sft as sft_mod
from .dedup import DedupIndex, dedup_jsonl
from .review import ReviewQueue
from .contamination import ContaminationScanner
from .gaming import GamingAnalyzer
from .reward import Reward
from .rollout import batch_rollout, do_rollout
from .task_spec import load_task
from .verifier import run_verifier, sha256_of_dir


def _results_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "results"


def cmd_oracle(args) -> int:
    task = load_task(args.task)
    t = do_rollout(task, f"{task.id}-oracle", mode="oracle",
                   results_dir=_results_dir())
    print(f"[oracle] verdict={t.verdict} tests={t.tests_passed}/{t.tests_total}")
    return 0 if t.verdict == "PASS" else 1


def cmd_rollout(args) -> int:
    batch_rollout(args.task, runs=args.runs, mode=args.mode,
                  agent_cmd=args.agent_cmd, results_dir=_results_dir())
    return 0


def cmd_export(args) -> int:
    jsonl = Path(args.jsonl) if args.jsonl else _results_dir() / "results.jsonl"
    stat = sft_mod.export_from_jsonl(jsonl, Path(args.out))
    print(f"[export] sft={stat['sft']} rlvr={stat['rlvr']} -> {args.out}")
    return 0


def cmd_dedup(args) -> int:
    jsonl = Path(args.jsonl) if args.jsonl else _results_dir() / "results.jsonl"
    stat = dedup_jsonl(jsonl, threshold=args.threshold)
    print(f"[dedup] kept={stat['kept']} removed={stat['removed']}")
    return 0


def cmd_review(args) -> int:
    jsonl = Path(args.jsonl) if args.jsonl else _results_dir() / "results.jsonl"
    n = ReviewQueue(jsonl).export_csv(Path(args.out))
    print(f"[review] pending items={n} -> {args.out}")
    return 0


def cmd_analyze(args) -> int:
    jsonl = Path(args.jsonl) if args.jsonl else _results_dir() / "results.jsonl"
    total = passed = failed = gaming = timeout = 0
    rewards = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        total += 1
        if d["verdict"] == "PASS":
            passed += 1
        else:
            failed += 1
        if d.get("failure_tag") == "gaming":
            gaming += 1
        if d.get("timed_out"):
            timeout += 1
        r = Reward.compute(d["verdict"], d["tests_passed"], d["tests_total"],
                           d.get("timed_out", False), d.get("failure_tag", "none"),
                           bool(d.get("agent_stdout", "").strip()))
        rewards.append(r.total)
    avg = sum(rewards) / len(rewards) if rewards else 0.0
    print(json.dumps({
        "total": total, "pass": passed, "fail": failed,
        "gaming_failures": gaming, "timeouts": timeout,
        "pass_rate": round(passed / total, 3) if total else 0,
        "avg_reward": round(avg, 3),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_scan(args) -> int:
    scanner = ContaminationScanner()
    for td in args.tasks:
        task = load_task(td)
        rep = scanner.scan_task(task)
        print(f"[{task.id}] {rep.verdict} {rep.note}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mini-tbench", description="coding agent RL 数据任务实验台")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("oracle", help="用 solution 校验 verifier")
    p.add_argument("--task", required=True)

    p = sub.add_parser("rollout", help="批量 rollout")
    p.add_argument("--task", required=True)
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--mode", choices=["agent", "oracle"], default="agent")
    p.add_argument("--agent-cmd", default=None)

    p = sub.add_parser("export", help="导出 SFT/RLVR")
    p.add_argument("--jsonl", default=None)
    p.add_argument("--out", default=str(_results_dir() / "train"))

    p = sub.add_parser("dedup", help="去重")
    p.add_argument("--jsonl", default=None)
    p.add_argument("--threshold", type=float, default=0.9)

    p = sub.add_parser("review", help="人工抽检清单")
    p.add_argument("--jsonl", default=None)
    p.add_argument("--out", default=str(_results_dir() / "review_queue.csv"))

    p = sub.add_parser("analyze", help="数据质量总览")
    p.add_argument("--jsonl", default=None)

    p = sub.add_parser("scan", help="污染指纹扫描")
    p.add_argument("--tasks", nargs="+", required=True)

    args = ap.parse_args(argv)
    return getattr(sys.modules[__name__], f"cmd_{args.cmd}")(args)


if __name__ == "__main__":
    raise SystemExit(main())
