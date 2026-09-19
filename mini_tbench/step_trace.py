"""step 级失败归因：把「任务失败」逆向拆解到「第一个不可恢复的 step」。

这是 JD 职责④里最难替代的能力，也是 reward 设计的输入：
  只知道「这条轨迹失败了」→ 只能做二值 reward；
  知道「失败发生在第 7 步的 edit_file，因为 agent 从未验证假设」→ 才能做
  过程奖励（process reward）、才能定向构造修正样本（rejection sampling）。

归因方法（golden-prefix 法）：
  1. 给每一步算「实质进展分」：写文件/成功执行 = 正，工具报错 = 负；
  2. 定位**最后一个有实质进展的 step**（golden prefix 的终点）；
  3. 其后的第一个「不可恢复错误」或「进入长期无进展」的 step，
     即 root cause step；
  4. 结合 stop_reason 与工具错误分布，落到具体类别。

类别体系（11 类，全部可定位到 step）：
  environment | tool_misuse | protocol_violation | loop | planning_error |
  premature_finish | no_verification | logic_error | gaming | timeout | none
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .agent_runtime import AgentRun, Step
from .verifier import ANTI_CHECK_IDS

# 顺序即优先级：越靠前越是「主导性」原因
CATEGORY_ORDER = [
    "gaming", "timeout", "environment", "protocol_violation", "tool_misuse",
    "loop", "premature_finish", "no_verification", "planning_error",
    "logic_error", "none",
]

CATEGORY_CN = {
    "environment": "环境/依赖/API 不可用",
    "tool_misuse": "工具误用（参数、路径、编辑锚点）",
    "protocol_violation": "未遵循输出协议（ReAct JSON）",
    "loop": "原地打转（重复同一动作）",
    "planning_error": "规划失败（步数耗尽 / 长期无进展）",
    "premature_finish": "过早宣称完成（未验证即收工）",
    "no_verification": "全程未做任何验证",
    "logic_error": "语义理解错误（认真做但做错）",
    "gaming": "钻空子（攻击 verifier）",
    "timeout": "超出时间上限",
    "none": "无失败",
}

_WRITE_TOOLS = {"write_file", "edit_file"}
_VERIFY_HINT = re.compile(
    r"pytest|unittest|python\s+-m\s+pytest|\bassert\b|--tb|nose|tox", re.I)

# 反作弊检查项统一由 verifier 定义（harness-executable 不在内——那是环境问题）
_ANTI_CHECK_IDS = ANTI_CHECK_IDS


@dataclass
class Attribution:
    run_id: str = ""
    category: str = "none"
    severity: str = "none"                 # none | low | medium | high
    root_cause_step: int | None = None
    root_cause_tool: str = ""
    evidence: list[str] = field(default_factory=list)
    secondary: list[str] = field(default_factory=list)
    first_error_step: int | None = None
    golden_prefix_len: int = 0             # 最后一个有实质进展的 step 序号
    no_progress_from: int | None = None
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _is_verify(cmd: str) -> bool:
    return bool(_VERIFY_HINT.search(cmd or ""))


def _progress_score(step: Step) -> int:
    """实质进展分：写 > 成功执行 > 只读 > 报错。"""
    if step.error or step.parse_failed:
        return -1
    if step.tool in _WRITE_TOOLS:
        return 2
    if step.tool == "bash":
        m = re.search(r"exit=(\d+)", step.observation or "")
        return 2 if (m and m.group(1) == "0") else 1
    if step.tool in ("read_file", "list_files"):
        return 1
    return 0


def _golden_prefix(steps: list[Step]) -> int:
    """最后一个有实质进展（score>=1）的 step 序号；无则 0。"""
    last = 0
    for s in steps:
        if _progress_score(s) >= 1:
            last = s.idx
    return last


def _first_error(steps: list[Step]) -> Step | None:
    for s in steps:
        if s.error or s.parse_failed:
            return s
    return None


def _stats(run: AgentRun) -> dict[str, Any]:
    steps = run.steps
    tools = Counter(s.tool for s in steps if s.tool and not s.tool.startswith("__"))
    errors = [s for s in steps if s.error]
    parse_fails = [s for s in steps if s.parse_failed]
    writes = [s for s in steps if s.tool in _WRITE_TOOLS and not s.error]
    bashes = [s for s in steps if s.tool == "bash" and not s.error]
    verifies = [s for s in bashes if _is_verify((s.args or {}).get("cmd", ""))]
    return {
        "n_steps": len(steps),
        "tool_hist": dict(tools),
        "n_errors": len(errors),
        "error_rate": round(len(errors) / len(steps), 3) if steps else 0.0,
        "n_parse_failed": len(parse_fails),
        "parse_fail_rate": round(len(parse_fails) / len(steps), 3) if steps else 0.0,
        "n_writes": len(writes),
        "n_bash": len(bashes),
        "n_verify_calls": len(verifies),
        "first_write_step": writes[0].idx if writes else None,
        "first_verify_step": verifies[0].idx if verifies else None,
        "obs_truncated": sum(1 for s in steps if s.obs_truncated),
    }


def attribute_failure(run_id: str, run: AgentRun, verdict: str,
                      checks: Iterable[dict] | None = None) -> Attribution:
    """把一次 rollout 的失败逆向归因到具体 step。verdict: PASS/FAIL。"""
    checks = list(checks or [])
    st = _stats(run)
    attr = Attribution(run_id=run_id, stats=st)
    steps = run.steps
    attr.golden_prefix_len = _golden_prefix(steps)
    fe = _first_error(steps)
    attr.first_error_step = fe.idx if fe else None

    if verdict == "PASS":
        attr.category, attr.severity = "none", "none"
        if st["n_errors"] >= 3:
            attr.severity = "low"
            attr.evidence.append(
                f"通过但伴随 {st['n_errors']} 次工具错误 → 高风险近失样本，适合做过程奖励")
        return attr

    # ---- 失败：按优先级判定主导类别 ----
    reasons: list[str] = []

    failed = [c for c in checks if not c.get("passed")]

    # 最高优先级：harness 自身没跑起来 —— 这条轨迹测量的是环境，不是模型能力。
    # 必须前置剔除，否则环境故障会被误判成 logic_error 污染数据分布。
    if any(c.get("id") == "harness-executable" for c in failed):
        attr.category = "environment"
        attr.severity = "medium"
        attr.evidence.append(
            "pytest 未产出可解析结果 → 环境/收集故障；该轨迹不得计入模型能力评估")
        return attr

    anti = [c for c in failed if c.get("id", "") in _ANTI_CHECK_IDS]
    if anti:
        attr.category = "gaming"
        ids = ", ".join(c["id"] for c in anti)
        attr.evidence.append(f"反作弊检查未通过：{ids}")
        # 定位到最早的写操作（作弊必然体现为某次写）
        w = [s for s in steps if s.tool in _WRITE_TOOLS]
        if w:
            attr.root_cause_step, attr.root_cause_tool = w[0].idx, w[0].tool

    if attr.category == "none" and run.stop_reason == "timeout":
        attr.category = "timeout"
        attr.evidence.append(f"总耗时 {run.elapsed}s 超上限")

    if attr.category == "none" and run.stop_reason == "api_error":
        attr.category = "environment"
        attr.root_cause_step = steps[-1].idx if steps else None
        attr.root_cause_tool = "__api__"
        attr.evidence.append("模型端点连续失败 → 属于环境而非 agent 能力")

    if attr.category == "none" and st["parse_fail_rate"] >= 0.3:
        attr.category = "protocol_violation"
        pf = [s for s in steps if s.parse_failed]
        attr.root_cause_step, attr.root_cause_tool = pf[0].idx, "__parse__"
        attr.evidence.append(
            f"{len(pf)}/{st['n_steps']} 步未输出合法 ReAct JSON（占比 {st['parse_fail_rate']:.0%}）")

    if attr.category == "none" and st["error_rate"] >= 0.3:
        attr.category = "tool_misuse"
        e = [s for s in steps if s.error and not s.parse_failed][0]
        attr.root_cause_step, attr.root_cause_tool = e.idx, e.tool
        attr.evidence.append(f"工具错误率 {st['error_rate']:.0%}；首个错误：{e.error[:160]}")

    if attr.category == "none" and run.stop_reason == "loop":
        attr.category = "loop"
        attr.root_cause_step = steps[-1].idx if steps else None
        attr.root_cause_tool = steps[-1].tool if steps else ""
        attr.evidence.append("连续重复同一 tool+args，被判为死循环")

    if attr.category == "none" and run.stop_reason == "max_steps":
        attr.category = "planning_error"
        attr.evidence.append(
            f"步数耗尽（{st['n_steps']} 步）仍未完成；最后一次实质进展在第 {attr.golden_prefix_len} 步")

    if attr.category == "none" and run.stop_reason == "finish":
        # 最关键的分叉：agent 是否在有验证的前提下收工
        if st["n_verify_calls"] == 0:
            attr.category = "premature_finish"
            attr.evidence.append(
                f"agent 在第 {st['n_steps']} 步宣告完成，但全程 0 次验证命令"
                f"（{st['n_bash']} 次 bash 均非测试类）")
        else:
            attr.category = "logic_error"
            attr.evidence.append(
                f"agent 跑了 {st['n_verify_calls']} 次验证仍失败 → 语义/口径理解错误，"
                f"首次验证在第 {st['first_verify_step']} 步")

    if attr.category == "none" and st["n_writes"] == 0:
        attr.category = "no_verification"
        attr.evidence.append("全程无任何文件写入 → agent 未产生实质动作")

    if attr.category == "none":
        attr.category = "logic_error"
        if attr.golden_prefix_len:
            attr.evidence.append(
                f"最后一次实质进展在第 {attr.golden_prefix_len} 步，此后未再推进")

    # root cause step 兜底
    if attr.root_cause_step is None:
        if attr.category in ("premature_finish", "loop", "planning_error"):
            attr.root_cause_step = steps[-1].idx if steps else None
            attr.root_cause_tool = steps[-1].tool if steps else ""
        elif attr.category == "logic_error":
            w = [s for s in steps if s.tool in _WRITE_TOOLS and not s.error]
            s_ = w[-1] if w else (steps[-1] if steps else None)
            attr.root_cause_step = s_.idx if s_ else None
            attr.root_cause_tool = s_.tool if s_ else ""

    # 无进展区间：golden prefix 之后第一个 -1 分的 step
    for s in steps:
        if s.idx > attr.golden_prefix_len and _progress_score(s) < 0:
            attr.no_progress_from = s.idx
            break

    # 次要标签（用于区分复合失败）
    sec: list[str] = []
    if st["n_parse_failed"] and attr.category != "protocol_violation":
        sec.append("protocol_violation")
    if st["n_verify_calls"] == 0 and attr.category not in (
            "premature_finish", "no_verification"):
        sec.append("no_verification")
    if st["n_errors"] >= 2 and attr.category != "tool_misuse":
        sec.append("tool_misuse")
    if st["obs_truncated"] >= 2:
        sec.append("context_overflow")
    attr.secondary = [x for x in sec if x != attr.category]

    attr.severity = ("high" if attr.category in
                     ("gaming", "premature_finish", "logic_error", "loop")
                     else "medium")
    attr.evidence = reasons + attr.evidence
    return attr


# ---------------------------------------------------------------------------
# 聚合：回答「你自己跑过多少条？成功率多少？失败集中在哪一步？」
# ---------------------------------------------------------------------------
def _bucket(idx: int | None) -> str:
    if idx is None:
        return "n/a"
    if idx <= 3:
        return "1-3"
    if idx <= 6:
        return "4-6"
    if idx <= 10:
        return "7-10"
    if idx <= 15:
        return "11-15"
    return "16+"


def _norm_record(r: dict) -> dict:
    """兼容两种输入形状：已扁平化的归因记录，或原始 Trajectory。

    早期版本假设调用方一定传扁平记录，结果批次跑完 30 条后在汇总阶段 KeyError，
    整轮 api 开销白费。汇总函数必须对上游形状宽容——它最容易在"什么都跑完了"
    之后才炸。
    """
    if "category" in r:
        return r
    att = r.get("attribution") or {}
    return {**r,
            "category": r.get("failure_tag") or "none",
            "root_cause_step": att.get("root_cause_step"),
            "root_cause_tool": att.get("root_cause_tool"),
            "stats": att.get("stats") or r.get("stats") or {}}


def aggregate(records: list[dict]) -> dict:
    """records: 扁平归因记录或原始 Trajectory 均可 → 可直接用于报告的聚合视图。"""
    records = [_norm_record(r) for r in records]
    n = len(records)
    if not n:
        return {"n": 0}
    passed = [r for r in records if r.get("verdict") == "PASS"]
    cats = Counter(r["category"] for r in records)
    step_buckets = Counter(_bucket(r.get("root_cause_step")) for r in records
                           if r.get("verdict") != "PASS")
    tool_hist = Counter(r.get("root_cause_tool") or "n/a" for r in records
                        if r.get("verdict") != "PASS")
    tool_use = Counter()
    total_steps = 0
    for r in records:
        th = (r.get("stats") or {}).get("tool_hist") or {}
        tool_use.update(th)
        total_steps += (r.get("stats") or {}).get("n_steps", 0)
    per_task: dict[str, dict] = {}
    for r in records:
        t = r.get("task", "?")
        d = per_task.setdefault(t, {"n": 0, "pass": 0})
        d["n"] += 1
        d["pass"] += 1 if r.get("verdict") == "PASS" else 0
    for d in per_task.values():
        d["pass_rate"] = round(d["pass"] / d["n"], 3) if d["n"] else 0.0
    return {
        "n": n,
        "n_pass": len(passed),
        "pass_rate": round(len(passed) / n, 3),
        "avg_steps": round(total_steps / n, 1),
        "category_dist": {k: cats[k] for k in CATEGORY_ORDER if cats.get(k)},
        "root_cause_step_bucket": dict(sorted(step_buckets.items())),
        "root_cause_tool": dict(tool_hist.most_common()),
        "tool_use_hist": dict(tool_use.most_common()),
        "per_task": per_task,
    }


def render_markdown(agg: dict, records: list[dict] | None = None,
                    title: str = "Agent 真实轨迹：失败归因报告") -> str:
    L: list[str] = [f"# {title}", ""]
    L += [f"- 轨迹条数：**{agg['n']}**",
          f"- 成功率：**{agg['pass_rate']:.1%}**（{agg['n_pass']}/{agg['n']}）",
          f"- 平均步数：**{agg['avg_steps']}**", ""]
    L += ["## 失败类别分布", "", "| 类别 | 条数 | 占比 |", "|---|---:|---:|"]
    for k, v in agg["category_dist"].items():
        L.append(f"| {k}（{CATEGORY_CN.get(k, k)}） | {v} | {v / agg['n']:.0%} |")
    L += ["", "## 失败发生位置（root cause step 分布）", "",
          "| 步区间 | 条数 |", "|---|---:|"]
    for k, v in agg["root_cause_step_bucket"].items():
        L.append(f"| 第 {k} 步 | {v} |")
    L += ["", "## 失败时的工具（root cause on which tool）", "",
          "| 工具 | 条数 |", "|---|---:|"]
    for k, v in agg["root_cause_tool"].items():
        L.append(f"| `{k}` | {v} |")
    L += ["", "## 分任务通过率", "", "| 任务 | 通过/总数 | 通过率 |", "|---|---:|---:|"]
    for t, d in agg["per_task"].items():
        L.append(f"| {t} | {d['pass']}/{d['n']} | {d['pass_rate']:.0%} |")
    if records:
        L += ["", "## 逐条明细", "",
              "| run_id | 任务 | 判定 | 类别 | root step | 工具 | 步数 |",
              "|---|---|---|---|---:|---|---:|"]
        for r in records:
            L.append("| {run_id} | {task} | {verdict} | {category} | {rcs} | `{rct}` | {ns} |".format(
                run_id=r.get("run_id", ""), task=r.get("task", ""),
                verdict=r.get("verdict", ""), category=r.get("category", ""),
                rcs=r.get("root_cause_step") or "-", rct=r.get("root_cause_tool") or "-",
                ns=(r.get("stats") or {}).get("n_steps", "-")))
    return "\n".join(L) + "\n"


def load_records(path: str) -> list[dict]:
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out
