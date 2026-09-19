#!/usr/bin/env python3
"""自托管 coding 模型的 ReAct agent 循环（替代 Claude Code CLI）。

调用 vLLM / SGLang 的 OpenAI 兼容端点，驱动模型完成一个任务：
工具：run_bash（在 /workspace 执行命令）、read_file、run_tests（pytest）。
循环到 finish_reason=stop / 达 max_steps / 触发长上下文压缩。

依赖：pip install openai
运行：OPENAI_BASE_URL=http://localhost:8000/v1 OPENAI_API_KEY=EMPTY \
      python serving/agent_loop.py --task tasks/task-01-fix-failing-tests
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import tiktoken
from pathlib import Path

from openai import OpenAI

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "在 /workspace 执行 bash 命令并返回 stdout/stderr",
            "parameters": {
                "type": "object",
                "properties": {"cmd": {"type": "string"}},
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取 /workspace 下文件内容",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "执行 pytest（仅作自检，verifier 在隔离容器重跑）",
            "parameters": {
                "type": "object",
                "properties": {"args": {"type": "string"}},
                "required": [],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "你是一个严谨的软件工程师，在 /workspace 内解决给定的编码任务。"
    "通过 run_bash 编辑文件、read_file 查看文件、run_tests 自检。"
    "修复必须基于业务语义，不得修改 tests 目录，不得硬编码期望输出。"
    "任务完成后用一句话汇报，不再调用工具。"
)


def _tok_count(text: str) -> int:
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4  # 兜底估算


class CodingAgent:
    def __init__(self, base_url: str, api_key: str, model: str,
                 workspace: Path, ctx_limit: int = 30000, ctx_summary_at: float = 0.8):
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.ws = workspace
        self.ctx_limit = ctx_limit
        self.ctx_summary_at = ctx_summary_at
        self.total_tokens = 0

    def _exec_tool(self, name: str, args: dict) -> str:
        if name == "run_bash":
            r = subprocess.run(args["cmd"], shell=True, cwd=self.ws,
                               capture_output=True, text=True, timeout=120)
            return (r.stdout + r.stderr)[:4000]
        if name == "read_file":
            p = self.ws / args["path"]
            return p.read_text()[:4000] if p.exists() else "FILE NOT FOUND"
        if name == "run_tests":
            r = subprocess.run("pytest -q", shell=True, cwd=self.ws,
                               capture_output=True, text=True, timeout=300)
            return (r.stdout + r.stderr)[:2000]
        return f"unknown tool {name}"

    def run(self, instruction: str, max_steps: int = 25) -> dict:
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": instruction}]
        trajectory, steps = [], 0
        t0 = time.time()
        while steps < max_steps:
            # 长上下文：接近上限时把历史压缩成摘要，仅保留最近一段
            if _tok_count(json.dumps(messages, ensure_ascii=False)) > self.ctx_limit * self.ctx_summary_at:
                messages = self._compress(messages)
            try:
                resp = self.client.chat.completions.create(
                    model=self.model, messages=messages, tools=TOOLS,
                    tool_choice="auto", temperature=0.2,
                )
            except Exception as e:  # 稳定性：服务抖动直接抛错给上层处理
                return self._final(trajectory, steps, t0, error=f"API_ERROR: {e}")
            self.total_tokens += resp.usage.total_tokens
            msg = resp.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))
            steps += 1
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    out = self._exec_tool(tc.function.name,
                                          json.loads(tc.function.arguments or "{}"))
                    trajectory.append({"step": steps, "tool": tc.function.name, "out": out[:500]})
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": out[:4000]})
            else:
                return self._final(trajectory, steps, t0)
        return self._final(trajectory, steps, t0, error="MAX_STEPS")

    def _compress(self, messages: list) -> list:
        tail = messages[-4:]
        summary = "（早期对话已压缩）任务进行中，以下是最近上下文：" + json.dumps(
            tail, ensure_ascii=False)[:2000]
        return [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": summary}]

    def _final(self, trajectory, steps, t0, error=None) -> dict:
        return {"steps": steps, "elapsed": round(time.time() - t0, 1),
                "total_tokens": self.total_tokens, "error": error,
                "trajectory": trajectory}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--model", default="coder")
    ap.add_argument("--max-steps", type=int, default=25)
    args = ap.parse_args()
    task_dir = Path(args.task).resolve()
    instruction = (task_dir / "task.yaml").read_text().split("instruction:")[1].split("rubric:")[0]
    agent = CodingAgent(
        base_url=os.environ.get("OPENAI_BASE_URL", "http://localhost:8000/v1"),
        api_key=os.environ.get("OPENAI_API_KEY", "EMPTY"),
        model=args.model, workspace=task_dir / "environment" / "workspace",
    )
    res = agent.run(instruction, max_steps=args.max_steps)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
