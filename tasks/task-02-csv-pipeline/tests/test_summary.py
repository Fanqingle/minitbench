"""Verifier：聚合口径验收（agent 不可见）。

测试自带 1 组干净样例 + 内部 golden；hidden 脏数据由 verifier 容器在运行期挂载，
本地演示用本文件样例即可。「无输入硬编码」由 harness 反作弊扫描 workspace 产物。
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "data" / "sales.csv"
GOLDEN = {"stores": {"S1": {"total": 100.0, "orders": 2},
                     "S2": {"total": 70.0, "orders": 2}}}


@pytest.fixture
def workspace(tmp_path):
    # 把样例拷贝到 workspace 根，模拟 agent 运行环境
    ws = tmp_path / "ws"
    ws.mkdir()
    shutil.copy(SAMPLE, ws / "sales.csv")
    sys.path.insert(0, str(ws))
    yield ws
    if str(ws) in sys.path:
        sys.path.remove(str(ws))


def test_output_matches_golden(workspace):
    import process
    process.main(str(workspace / "sales.csv"), str(workspace / "summary.json"))
    got = json.loads((workspace / "summary.json").read_text(encoding="utf-8"))
    assert got == GOLDEN, f"got={got}\nexpected={GOLDEN}"


def test_deterministic(workspace):
    import importlib
    import process
    process.main(str(workspace / "sales.csv"), str(workspace / "s1.json"))
    process.main(str(workspace / "sales.csv"), str(workspace / "s2.json"))
    a = (workspace / "s1.json").read_bytes()
    b = (workspace / "s2.json").read_bytes()
    assert a == b, "同输入两次输出不一致（引入随机性/字典序问题）"
