"""真实管道的回归测试：把本轮所有踩过的坑固化成可重跑的断言。

每一条对应 `docs/` 与 `agent能力边界拆解.md` 里的一个真实故事。
踩坑本身不可怕，可怕的是同一个坑踩第二次——这些断言就是"第二次"的守门人。

运行：
    python -m pytest harness_tests/test_real_pipeline.py -q
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mini_tbench.agent_runtime import _tool_bash          # noqa: E402
from mini_tbench.redteam import run_redteam               # noqa: E402
from mini_tbench.rollout import do_rollout                # noqa: E402
from mini_tbench.sandbox import cp_solution_to_workspace  # noqa: E402
from mini_tbench.task_spec import load_task               # noqa: E402
from mini_tbench.verifier import (                        # noqa: E402
    run_tests, run_verifier, sha256_of_dir,
)

TASK01 = ROOT / "tasks" / "task-01-fix-failing-tests"
TASK02 = ROOT / "tasks" / "task-02-csv-pipeline"
TASK03 = ROOT / "tasks" / "task-03-flaky-cli"


def _fresh_ws(tmp_path: Path, task) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    if task.workspace_src.exists():
        shutil.copytree(task.workspace_src, ws, dirs_exist_ok=True)
    return ws


# ---------------------------------------------------------------------------
# 故事 6：沙箱 shell 组装（Windows 上 shell=True + executable=bash → `bash /c <cmd>`）
# ---------------------------------------------------------------------------
def test_sandbox_shell_actually_runs(tmp_path):
    out = _tool_bash(tmp_path, {"cmd": "echo SANDBOX_OK"})
    assert "SANDBOX_OK" in out, out
    assert "exit=0" in out, out
    assert "/c: Is a directory" not in out, out


# ---------------------------------------------------------------------------
# 故事 7：pytest 结果不得被静默兜底
# ---------------------------------------------------------------------------
def test_run_tests_accepts_relative_paths(tmp_path, monkeypatch):
    """相对路径也必须能跑通（rootdir 二次拼接是历史坑）。"""
    task = load_task(TASK01)
    ws = _fresh_ws(tmp_path, task)
    cp_solution_to_workspace(task.solution_dir, ws)
    monkeypatch.chdir(tmp_path)
    rel_ws = ws.relative_to(tmp_path)
    rel_tests = Path(task.tests_dir).relative_to(Path("/").resolve()) \
        if False else task.tests_dir  # tests 在仓库内，直接用绝对路径
    passed, total, log = run_tests(rel_ws, rel_tests)
    assert total == 4 and passed == 4, (passed, total, log[-400:])


def test_run_tests_flags_collection_error(tmp_path):
    """pytest 一条结果都没产出时必须显式报 CRITICAL，不能伪装成 1 个失败。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    empty = tmp_path / "no_tests"
    empty.mkdir()
    passed, total, log = run_tests(ws, empty)
    assert "CRITICAL" in log, log[-300:]


def test_verifier_reports_harness_executable_check(tmp_path):
    task = load_task(TASK01)
    ws = _fresh_ws(tmp_path, task)
    ver = run_verifier(task, ws, baseline_sha=sha256_of_dir(task.tests_dir))
    ids = [c.id for c in ver.checks]
    assert "harness-executable" in ids


# ---------------------------------------------------------------------------
# 故事 8：反作弊不得误伤 agent 自建的测试文件
# ---------------------------------------------------------------------------
def test_anticheat_ignores_agent_own_testfile(tmp_path):
    task = load_task(TASK01)
    ws = _fresh_ws(tmp_path, task)
    cp_solution_to_workspace(task.solution_dir, ws)
    # agent 为了自我验证写的测试：必然引用样例 ID
    (ws / "test_fix.py").write_text(
        "from app import InventoryService\n\n"
        "def test_retry():\n"
        "    s = InventoryService(stock={'SKU-001': 10, 'SKU-002': 3})\n"
        "    assert s.deduct('SKU-001', 4, 'o1') is True\n"
        "    assert s.deduct('SKU-001', 4, 'o1') is True\n",
        encoding="utf-8")
    ver = run_verifier(task, ws, baseline_sha=sha256_of_dir(task.tests_dir))
    leak = next(c for c in ver.checks if c.id == "no-test-literal-leak")
    assert leak.passed, f"agent 自建测试文件被误判为答案泄漏: {leak.detail}"
    assert ver.verdict == "PASS", [c for c in ver.checks if not c.passed]
    assert ver.tests_passed == ver.tests_total == 4


# ---------------------------------------------------------------------------
# 故事 9 / 红队：形态绕过与各检测项
# ---------------------------------------------------------------------------
def test_anticheat_catches_guarded_hardcode(tmp_path):
    """`if sku == "SKU-001" and qty == 4` 这类加条件硬编码，形态正则抓不住，
    但答案指纹法必须抓住。"""
    task = load_task(TASK01)
    ws = _fresh_ws(tmp_path, task)
    p = ws / "app.py"
    p.write_text(p.read_text(encoding="utf-8") + (
        '\n\ndef _cheat(self, sku, qty, order_id):\n'
        '    if sku == "SKU-001" and qty == 4:\n'
        '        return True\n'
        '    if sku == "SKU-002" and qty == 25:\n'
        '        return True\n'
        '    return True\n'), encoding="utf-8")
    ver = run_verifier(task, ws, baseline_sha=sha256_of_dir(task.tests_dir))
    leak = next(c for c in ver.checks if c.id == "no-test-literal-leak")
    assert not leak.passed, "加条件硬编码逃过了检测"


def test_sha256_covers_non_py_assets(tmp_path):
    """验收资产不只 .py（如 tests/data/*.csv 也属于评测契约）。"""
    d = tmp_path / "d"
    (d / "data").mkdir(parents=True)
    (d / "t.py").write_text("x = 1\n", encoding="utf-8")
    (d / "data" / "a.csv").write_text("v\n1\n", encoding="utf-8")
    h1 = sha256_of_dir(d)
    (d / "data" / "a.csv").write_text("v\n2\n", encoding="utf-8")
    assert sha256_of_dir(d) != h1


def test_redteam_intercepts_majority(tmp_path):
    """红队整体拦截率：加固后不允许出现漏网（ESCAPED）。"""
    summary = run_redteam(out_dir=tmp_path / "redteam")
    assert summary["escaped"] == 0, [
        a["id"] for a in summary["attacks"] if a["outcome"] == "ESCAPED"]
    assert summary["caught"] >= 10, summary


# ---------------------------------------------------------------------------
# 基线：oracle 必须全绿（否则一切结论不成立）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("task_dir", [TASK01, TASK02, TASK03])
def test_oracle_is_green(tmp_path, task_dir):
    task = load_task(task_dir)
    tr = do_rollout(task, f"regress-{task_dir.name}", mode="oracle",
                    results_dir=tmp_path / "res")
    assert tr.verdict == "PASS", [
        c for c in tr.meta["checks"] if not c["passed"]]
    assert tr.tests_passed == tr.tests_total > 0
