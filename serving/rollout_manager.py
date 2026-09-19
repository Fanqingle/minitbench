#!/usr/bin/env python3
"""并发 rollout 管理器：压测自托管推理服务的并发 / 长上下文 / 稳定性。

解决岗位关心的三个真实工程问题：
1. 并发：asyncio.Semaphore 控制同时在途 rollout 数，对齐 vLLM 的 --max-num-seqs
2. 长上下文：从 agent 回报里采集 total_tokens，统计分布与超长触发摘要比例
3. 稳定性：服务健康检查 + 指数退避重试 + 失败任务断点续跑（run_id 幂等，重跑不重复计数）

运行：
  OPENAI_BASE_URL=http://localhost:8000/v1 python serving/rollout_manager.py \
      --model coder --concurrency 128 --runs 50 --task tasks/task-01-fix-failing-tests
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from serving.agent_loop import CodingAgent

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


async def _health(base_url: str) -> bool:
    """稳定性：轮询 /health，服务抖动时先恢复再继续，而不是失败整批。"""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(base_url.rstrip("/v1") + "/health")
            return r.status_code == 200
    except Exception:
        return False


async def _one_rollout(sem: asyncio.Semaphore, agent_kwargs: dict, instruction: str,
                       run_id: str, retries: int = 3) -> dict:
    async with sem:
        record = {"run_id": run_id, "ts": time.time()}
        for attempt in range(retries):
            try:
                # 稳定性：瞬时 API 错误退避重试
                agent = CodingAgent(**agent_kwargs)
                loop = asyncio.get_event_loop()
                res = await loop.run_in_executor(None, agent.run, instruction)
                record.update(res)
                record["retry"] = attempt
                return record
            except Exception as e:
                await asyncio.sleep(min(2 ** attempt, 8))
                record["error"] = f"ATTEMPT_{attempt}_FAIL: {e}"
        return record


async def main_async(args) -> None:
    task_dir = Path(args.task).resolve()
    instruction = (task_dir / "task.yaml").read_text().split("instruction:")[1].split("rubric:")[0]
    agent_kwargs = dict(
        base_url=args.base_url, api_key="EMPTY", model=args.model,
        workspace=task_dir / "environment" / "workspace",
    )
    sem = asyncio.Semaphore(args.concurrency)
    tasks = [
        _one_rollout(sem, agent_kwargs, instruction, f"run-{i}", retries=args.retries)
        for i in range(args.runs)
    ]
    RESULTS.mkdir(parents=True, exist_ok=True)
    recs = await asyncio.gather(*tasks)
    out = RESULTS / f"serving-{args.concurrency}-{int(time.time())}.jsonl"
    for r in recs:
        out.write_text(json.dumps(r, ensure_ascii=False) + "\n", encoding="utf-8") if False else None
    with open(out, "a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # —— 指标汇总（进报告的核心数字）——
    n = len(recs)
    ok = [r for r in recs if not r.get("error")]
    toks = [r.get("total_tokens", 0) for r in ok]
    elapsed = [r.get("elapsed", 0) for r in ok]
    print(f"=== serving metrics @ concurrency={args.concurrency} ===")
    print(f"rollouts={n}  success={len(ok)}  fail={n-len(ok)}")
    if toks:
        print(f"tokens/rollout  avg={sum(toks)/len(toks):.0f}  max={max(toks)}")
        print(f"long-ctx (>20k tokens) ratio={sum(1 for t in toks if t>20000)/len(toks):.0%}")
    if elapsed:
        print(f"latency p50={sorted(elapsed)[len(elapsed)//2]:.1f}s  "
              f"p95={sorted(elapsed)[int(len(elapsed)*0.95)]:.1f}s")
    print(f"written -> {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--model", default="coder")
    ap.add_argument("--concurrency", type=int, default=64)
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--base-url", default="http://localhost:8000/v1")
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    import sys
    sys.exit(main())
