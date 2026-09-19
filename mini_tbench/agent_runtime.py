"""真实 agent 运行时：真实 LLM 驱动 ReAct 循环，逐步留痕。

这是 Mini-TBench 从「可复现演示」升级为「真实数据管道」的关键替换件——
把 rollout 里注入的确定性 mock 函数，换成真实模型在真实工作区里的真实执行。

为什么不用现成 agent 框架（OpenAI Agents SDK / LangGraph / smolagents）：
  本项目的交付物是 post-training 数据，需要的不是「agent 能跑通」，
  而是**可控可观测的 rollout 基座**：每一步的 thought / tool / args /
  observation / error / token 都要暴露给 reward 与 step 级失败归因。
  框架把 step 语义封装在内部，恰好拿不到这个粒度。
  另外客户环境多为内网隔离，必须做到「改一个 base_url 就指向自托管 vLLM/SGLang」。

后端：任意 OpenAI 兼容端点（DashScope / ARK / DeepSeek / 自托管 vLLM、SGLang）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 后端解析
# ---------------------------------------------------------------------------
BACKENDS: dict[str, tuple[str | None, str | None]] = {
    # name -> (default_base_url, api_key_env)
    "dashscope": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
    "ark": ("https://ark.cn-beijing.volces.com/api/v3", "ARK_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "vllm": (None, None),  # 自托管，必须显式给 base_url
}

DEFAULT_MAX_STEPS = 24
_MAX_OBS_CHARS = 3000
_MAX_RAW_CHARS = 2000
_BASH_TIMEOUT = 60
_API_RETRIES = 3

_SYSTEM_PROMPT = """You are a coding agent operating inside a sandboxed workspace.

You work in strict ReAct steps. In EVERY turn you must output EXACTLY ONE JSON object
and nothing else (no markdown fence, no prose outside the JSON):

{"thought": "<your reasoning, 1-3 sentences>", "tool": "<tool_name>", "args": {<args>}}

Available tools:
- list_files : {"path": "."}                      list files under a path (recursive, depth-limited)
- read_file  : {"path": "app.py", "start": 1, "end": 200}
- write_file : {"path": "app.py", "content": "..."}   overwrite file
- edit_file  : {"path": "app.py", "old": "...", "new": "..."}  exact string replace, must be unique
- bash       : {"cmd": "python -m pytest -q"}     run a shell command in the workspace root
- finish     : {"answer": "<summary of what you changed>"}

Rules:
1. All paths are relative to the workspace root. Never use absolute paths.
2. You cannot see the hidden acceptance tests. Solve for the *semantics*, not the sample data.
3. Verify your work by running the code yourself before calling finish.
4. Do not stop until you have either fixed the problem or exhausted your ideas.
"""

_TOOLS = ("list_files", "read_file", "write_file", "edit_file", "bash", "finish")

# 纯终端形态（Terminal-Bench / Harbor 官方任务）。刻意收敛工具集：
# 官方环境里 agent 只有一个 shell，给它 read_file/write_file 这类结构化工具
# 会让分数与官方 leaderboard 不可比——那等于换了把更顺手的锤子再比谁敲得快。
_SYSTEM_PROMPT_TERMINAL = """You are a terminal agent operating inside a sandboxed Linux container.

You work in strict ReAct steps. In EVERY turn you must output EXACTLY ONE JSON object
and nothing else (no markdown fence, no prose outside the JSON):

{"thought": "<your reasoning, 1-3 sentences>", "tool": "bash", "args": {"cmd": "..."}}

Available tools:
- bash   : {"cmd": "ls -la /app"}   run a shell command inside the container
- finish : {"answer": "<summary>"}  declare the task complete

Rules:
1. You only have a shell. Use it (cat / echo / sed / python3) to inspect and edit files.
2. The container may be fully offline: do not assume you can download anything.
3. Work incrementally and verify progress with real commands, not assumptions.
4. Keep going until the task is genuinely complete; do not stop early.
"""


# ---------------------------------------------------------------------------
# HTTP (标准库实现，零第三方依赖，内网环境可直接落地)
# ---------------------------------------------------------------------------
class ApiError(RuntimeError):
    pass


def _resolve_endpoint(backend: str, base_url: str | None, api_key: str | None
                      ) -> tuple[str, str]:
    if base_url:
        url = base_url.rstrip("/")
        key = api_key or os.environ.get("OPENAI_API_KEY", "")
    else:
        if backend not in BACKENDS:
            raise ApiError(f"unknown backend {backend!r}; known={list(BACKENDS)}")
        default_url, key_env = BACKENDS[backend]
        if not default_url:
            raise ApiError(f"backend {backend!r} requires an explicit base_url")
        url, key = default_url, (api_key or os.environ.get(key_env or "", ""))
    if not url.endswith("/v1") and "/compatible-mode" not in url and backend != "ark":
        pass  # 允许用户传任意已带前缀的 URL，不做强制改写
    if not key:
        raise ApiError(f"no API key for backend={backend}; set the corresponding env var")
    return url, key


def complete(messages: list[dict], *, backend: str = "dashscope",
             model: str = "qwen3-coder-plus", base_url: str | None = None,
             api_key: str | None = None, temperature: float = 0.7,
             max_tokens: int = 4096, timeout: int = 180) -> tuple[str, dict[str, int]]:
    """单次 chat completion，返回 (text, usage)。带指数退避重试。"""
    url, key = _resolve_endpoint(backend, base_url, api_key)
    payload = json.dumps({
        "model": model, "messages": messages,
        "temperature": temperature, "max_tokens": max_tokens,
    }).encode("utf-8")
    last: Exception | None = None
    for attempt in range(_API_RETRIES):
        req = urllib.request.Request(
            url + "/chat/completions", data=payload,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            return d["choices"][0]["message"]["content"] or "", (d.get("usage") or {})
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                KeyError, json.JSONDecodeError) as e:
            detail = ""
            if isinstance(e, urllib.error.HTTPError):
                try:
                    detail = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
            last = ApiError(f"{type(e).__name__}: {e} {detail}")
            if attempt < _API_RETRIES - 1:
                time.sleep(1.5 * (2 ** attempt))
    raise last or ApiError("unknown api failure")


# ---------------------------------------------------------------------------
# 工具层（沙箱内执行，带路径守卫）
# ---------------------------------------------------------------------------
class ToolError(Exception):
    """工具层错误——是 tool_misuse 类失败的一手信号，必须留痕而非吞掉。"""


def _resolve(workspace: Path, rel: str) -> Path:
    """路径守卫：只允许 workspace 内的路径，杜绝 agent 逃逸到宿主文件系统。"""
    ws = workspace.resolve()
    cand = Path(rel)
    if cand.is_absolute():
        raise ToolError(f"absolute path not allowed: {rel}")
    p = (ws / cand).resolve()
    if p != ws and ws not in p.parents:
        raise ToolError(f"path escapes workspace: {rel}")
    return p


def _shell() -> str | None:
    return shutil.which("bash")


def _run_shell(cmd: str, cwd: Path, env: dict, timeout: int):
    """跨平台 shell 执行。

    踩坑记录（真实案例，已固化为回归点）：
      Windows 上 `subprocess.run(cmd, shell=True, executable="<git-bash>")`
      会被组装成 `<git-bash> /c <cmd>`，于是每个命令都返回
      `exit=126 /c: Is a directory`。
      后果远比"命令跑不了"更严重：agent 无法自我验证 → 失败被错误地
      归因成 logic_error，而真实根因是 environment。
      这正是数据质量门禁必须把环境类失败前置剔除的原因——
      否则整批轨迹都在测量 harness，而不是测量模型。
    修法：Windows 下显式传 argv（list 形式），绕开 shell=True 的 /c 组装。
    """
    sh = _shell()
    if sh:
        return subprocess.run([sh, "-lc", cmd], cwd=str(cwd), capture_output=True,
                              text=True, timeout=timeout, env=env)
    return subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True,
                          text=True, timeout=timeout, env=env)


def _bounded(text: str, limit: int = _MAX_OBS_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head = text[: int(limit * 0.6)]
    tail = text[-int(limit * 0.3):]
    return f"{head}\n...[{len(text) - limit} chars elided]...\n{tail}", True


def _prepend_interpreter_dir(path: str) -> str:
    """把 harness 解释器所在目录置于 PATH 最前，使 agent 与 verifier 同环境。

    实测踩到的坑：pytest 等任务依赖装在 harness 的解释器里，而 agent 执行
    `python3 -m pytest` 走 PATH 命中的是**另一个**解释器（系统 python），
    于是报 "No module named pytest"。后果不只是多花几步——agent 会因此
    放弃跑测试、退化成「盲改」，轨迹里表现为 environment 类噪音掩盖了
    真实的 logic_error。让两者指向同一解释器，这条噪音才消失。
    """
    d = str(Path(sys.executable).resolve().parent)
    parts = [p for p in (path or "").split(os.pathsep) if p]
    if d in parts:
        return path
    return os.pathsep.join([d, *parts])


def _tool_bash(ws: Path, args: dict) -> str:
    cmd = str(args.get("cmd", "")).strip()
    if not cmd:
        raise ToolError("bash: empty cmd")
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PATH"] = _prepend_interpreter_dir(env.get("PATH", ""))
    # 供任务 README / 指令引用：显式给一个与 verifier 同源的解释器
    env["MTB_PY"] = sys.executable
    try:
        r = _run_shell(cmd, ws, env, _BASH_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ToolError(f"bash: timeout after {_BASH_TIMEOUT}s")
    out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
    return f"exit={r.returncode}\n{out.strip() or '(no output)'}"


def _tool_list_files(ws: Path, args: dict) -> str:
    root = _resolve(ws, args.get("path", "."))
    if not root.exists():
        raise ToolError(f"list_files: not found: {args.get('path')}")
    ws_res = ws.resolve()
    lines: list[str] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(ws_res)
        if len(rel.parts) > 4:
            continue
        if any(part in {".git", "__pycache__", ".pytest_cache"} for part in rel.parts):
            continue
        lines.append(f"{rel.as_posix()}{'/' if p.is_dir() else ''}")
    return "\n".join(lines[:200]) or "(empty)"


def _tool_read_file(ws: Path, args: dict) -> str:
    p = _resolve(ws, args.get("path", ""))
    if not p.is_file():
        raise ToolError(f"read_file: not a file: {args.get('path')}")
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start = int(args.get("start") or 1)
    end = int(args.get("end") or len(lines))
    sel = lines[max(0, start - 1): end]
    body = "\n".join(f"{i + start:>4}| {ln}" for i, ln in enumerate(sel))
    return f"{p.relative_to(ws.resolve()).as_posix()} ({len(lines)} lines total)\n{body}"


def _tool_write_file(ws: Path, args: dict) -> str:
    p = _resolve(ws, args.get("path", ""))
    content = args.get("content")
    if content is None:
        raise ToolError("write_file: missing 'content'")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(content), encoding="utf-8")
    return f"wrote {len(str(content))} chars to {p.relative_to(ws.resolve()).as_posix()}"


def _tool_edit_file(ws: Path, args: dict) -> str:
    p = _resolve(ws, args.get("path", ""))
    if not p.is_file():
        raise ToolError(f"edit_file: not a file: {args.get('path')}")
    old, new = args.get("old"), args.get("new")
    if not old:
        raise ToolError("edit_file: missing 'old'")
    text = p.read_text(encoding="utf-8", errors="replace")
    n = text.count(str(old))
    if n == 0:
        raise ToolError("edit_file: 'old' string not found (read the file again)")
    if n > 1:
        raise ToolError(f"edit_file: 'old' string is not unique ({n} matches)")
    p.write_text(text.replace(str(old), str(new or ""), 1), encoding="utf-8")
    return f"patched {p.relative_to(ws.resolve()).as_posix()}"


_TOOL_IMPL = {
    "bash": _tool_bash, "list_files": _tool_list_files,
    "read_file": _tool_read_file, "write_file": _tool_write_file,
    "edit_file": _tool_edit_file,
}


# ---------------------------------------------------------------------------
# 协议解析
# ---------------------------------------------------------------------------
_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def parse_action(text: str) -> tuple[dict | None, str]:
    """尽力解析模型输出为 action dict。返回 (action, error_reason)。"""
    candidates: list[str] = []
    stripped = (text or "").strip()
    if stripped:
        candidates.append(stripped)
    candidates += _FENCE.findall(text or "")
    # 平衡花括号扫描
    s = text or ""
    for i, ch in enumerate(s):
        if ch != "{":
            continue
        depth = 0
        for j in range(i, len(s)):
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(s[i: j + 1])
                    break
    for c in candidates:
        try:
            d = json.loads(c)
        except Exception:
            continue
        if isinstance(d, dict) and ("tool" in d or "action" in d):
            tool = d.get("tool") or d.get("action")
            args = d.get("args") or d.get("action_input") or {}
            if not isinstance(args, dict):
                args = {"value": args}
            return ({"thought": str(d.get("thought", ""))[:1200],
                     "tool": str(tool).strip(), "args": args}, "")
    return None, "could not parse a JSON action from your output"


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class Step:
    idx: int
    thought: str = ""
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    observation: str = ""
    obs_truncated: bool = False
    error: str | None = None          # 工具层错误原文（tool_misuse 一手信号）
    parse_failed: bool = False        # 模型没按协议输出（协议遵循失败的信号）
    elapsed: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)
    raw_response: str = ""            # SFT 语料原料（截断）

    @property
    def ok(self) -> bool:
        return self.error is None and not self.parse_failed


@dataclass
class AgentRun:
    ok: bool
    steps: list[Step] = field(default_factory=list)
    final_answer: str = ""
    stop_reason: str = "unknown"      # finish | max_steps | api_error | loop | timeout
    elapsed: float = 0.0
    usage_total: dict[str, int] = field(default_factory=dict)
    model: str = ""
    backend: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["steps"] = [asdict(s) for s in self.steps]
        return d


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------
def run_agent(workspace: Path, instruction: str, *, backend: str = "dashscope",
              model: str = "qwen3-coder-plus", base_url: str | None = None,
              api_key: str | None = None, max_steps: int = DEFAULT_MAX_STEPS,
              temperature: float = 0.7, total_timeout: int = 900,
              log: bool = False, executor=None,
              workdir: str = "/app") -> AgentRun:
    """跑一次真实 agent rollout，返回逐步留痕的 AgentRun。

    executor=None      → 本地工作区模式（结构化工具集：读/写/编辑/bash）。
    executor=DockerExecutor → 纯终端容器模式，工具集收敛为 {bash, finish}，
                        所有动作经 `docker exec` 进入真实沙箱，用于跑
                        Terminal-Bench / Harbor 官方任务集。
    """
    terminal_only = executor is not None
    tools_allowed = ("bash", "finish") if terminal_only else _TOOLS
    workspace = Path(workspace or ".").resolve()
    ws_label = workdir if terminal_only else (
        "/workspace" if os.name != "nt" else "the workspace root")
    user = (f"Task:\n{instruction.strip()}\n\n"
            f"Your shell working directory is {ws_label}.\n"
            f"Start by inspecting the environment, then do the work. "
            f"You have at most {max_steps} steps.")
    messages: list[dict] = [
        {"role": "system",
         "content": _SYSTEM_PROMPT_TERMINAL if terminal_only else _SYSTEM_PROMPT},
        {"role": "user", "content": user}]
    steps: list[Step] = []
    usage_total: dict[str, int] = {}
    t0 = time.time()
    stop_reason = "max_steps"
    final_answer = ""
    last_sig: tuple | None = None
    repeat = 0
    api_fail = 0

    for idx in range(1, max_steps + 1):
        if time.time() - t0 > total_timeout:
            stop_reason = "timeout"
            break
        try:
            text, usage = complete(messages, backend=backend, model=model,
                                   base_url=base_url, api_key=api_key,
                                   temperature=temperature)
            api_fail = 0
        except ApiError as e:
            api_fail += 1
            steps.append(Step(idx=idx, error=f"api_error: {e}", tool="__api__"))
            if api_fail >= 2:
                stop_reason = "api_error"
                break
            continue

        for k, v in (usage or {}).items():
            if isinstance(v, int):
                usage_total[k] = usage_total.get(k, 0) + v

        action, perr = parse_action(text)
        if action is None:
            st = Step(idx=idx, parse_failed=True, tool="__parse__", error=perr,
                      raw_response=text[:_MAX_RAW_CHARS], usage=usage or {})
            steps.append(st)
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content":
                             f"Observation: PROTOCOL ERROR — {perr}. "
                             f"Output exactly one JSON object with keys thought/tool/args."})
            continue

        tool = action["tool"]
        args = action["args"]
        sig = (tool, json.dumps(args, sort_keys=True, ensure_ascii=False)[:400])
        repeat = repeat + 1 if sig == last_sig else 0
        last_sig = sig

        st = Step(idx=idx, thought=action["thought"], tool=tool, args=args,
                  raw_response=text[:_MAX_RAW_CHARS], usage=usage or {})
        t_step = time.time()

        if tool == "finish":
            st.observation = "(agent declared completion)"
            st.elapsed = round(time.time() - t_step, 2)
            steps.append(st)
            final_answer = str(args.get("answer", ""))
            stop_reason = "finish"
            break

        if tool not in tools_allowed:
            st.error = f"unknown tool {tool!r}; available={list(tools_allowed)}"
            st.observation = f"Observation: ERROR — {st.error}"
        else:
            try:
                if terminal_only and tool == "bash":
                    cmd = str(args.get("cmd", "")).strip()
                    if not cmd:
                        raise ToolError("bash: empty cmd")
                    r = executor.run(cmd, timeout=_BASH_TIMEOUT)
                    st.observation, st.obs_truncated = _bounded(
                        f"exit={r.returncode}\n{r.output}")
                    # 非零退出码不算 step 错误（agent 会试探性跑命令，grep 无匹配也非零）；
                    # 只有执行器超时才计入 error，避免 error_rate 虚高把失败误归成 tool_misuse
                    if r.timed_out:
                        st.error = f"bash: timeout after {_BASH_TIMEOUT}s"
                else:
                    obs = _TOOL_IMPL[tool](workspace, args)
                    st.observation, st.obs_truncated = _bounded(obs)
            except ToolError as e:
                st.error = str(e)
                st.observation = f"Observation: ERROR — {st.error}"
            except Exception as e:  # 工具崩溃也要留痕
                st.error = f"{type(e).__name__}: {e}"
                st.observation = f"Observation: ERROR — {st.error}"
        st.elapsed = round(time.time() - t_step, 2)
        steps.append(st)

        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": st.observation})

        if repeat >= 2:
            stop_reason = "loop"
            break

    ok = stop_reason == "finish"
    return AgentRun(ok=ok, steps=steps, final_answer=final_answer,
                    stop_reason=stop_reason, elapsed=round(time.time() - t0, 2),
                    usage_total=usage_total, model=model, backend=backend)


def run_agent_cmd(instruction_file: str) -> int:
    """CLI 入口（供 rollout 通过子进程调用，保证 agent 与 harness 进程隔离）。"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--instruction", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--backend", default="dashscope")
    ap.add_argument("--model", default="qwen3-coder-plus")
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    ap.add_argument("--temperature", type=float, default=0.7)
    a = ap.parse_args()
    run = run_agent(Path(a.workspace), Path(a.instruction).read_text(encoding="utf-8"),
                    backend=a.backend, model=a.model, base_url=a.base_url,
                    max_steps=a.max_steps, temperature=a.temperature)
    Path(a.out).write_text(json.dumps(run.to_dict(), ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(json.dumps({"ok": run.ok, "stop_reason": run.stop_reason,
                      "steps": len(run.steps), "elapsed": run.elapsed,
                      "usage": run.usage_total}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_agent_cmd(None))  # type: ignore[arg-type]
