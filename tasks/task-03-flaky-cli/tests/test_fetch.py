"""Verifier：故障注入环境下的鲁棒性验收（agent 不可见注入器）。

本地演示用 monkeypatch 模拟故障注入器：前 2 次调用失败、之后成功（确定性）。
真实 Docker verifier 会挂载一个 40% 概率抛错、固定种子的注入器。

注意：本测试通过 verifier 注入的 PYTHONPATH（=workspace）导入 fetch_cli，
不依赖任何绝对路径，因此 oracle 解法被拷入 workspace 后测试自然指向 oracle。
"""
import json
import urllib.error

import pytest

RECORDS = [{"id": str(i), "v": i} for i in range(10)]
RECORDS_TEXT = "\n".join(json.dumps(r, ensure_ascii=False) for r in RECORDS)


class FakeNet:
    """确定性故障注入器：前 fail_times 次抛错，之后返回完整记录。"""
    def __init__(self, fail_times=2):
        self.n = 0
        self.fail_times = fail_times

    def __call__(self, url):
        self.n += 1
        if self.n <= self.fail_times:
            raise TimeoutError("injected timeout")
        return RECORDS_TEXT


@pytest.fixture
def fake_net(monkeypatch):
    fn = FakeNet(fail_times=2)
    import fetch_cli
    monkeypatch.setattr(fetch_cli, "get", fn)
    return fn


def test_survives_fault_injection(tmp_path, fake_net):
    import fetch_cli
    out = tmp_path / "out.jsonl"
    res = fetch_cli.run("http://fake/records", str(out), records_target=10)
    assert res["ok"] is True, res
    lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 10
    assert len({r["id"] for r in lines}) == 10, "存在重复记录 id"


def test_idempotent_on_duplicate(tmp_path, monkeypatch):
    """响应重复投递：客户端必须按 id 去重。"""
    import fetch_cli
    dup = "\n".join(json.dumps(r, ensure_ascii=False) for r in RECORDS + RECORDS)

    class AlwaysOK:
        def __call__(self, url):
            return dup

    monkeypatch.setattr(fetch_cli, "get", AlwaysOK())
    out = tmp_path / "out.jsonl"
    res = fetch_cli.run("http://fake", str(out), records_target=10)
    assert res["ok"] is True
    lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len({r["id"] for r in lines}) == 10


def test_partial_failure_returns_code(tmp_path, monkeypatch):
    import fetch_cli

    class AlwaysFail:
        def __call__(self, url):
            raise TimeoutError("always")

    monkeypatch.setattr(fetch_cli, "get", AlwaysFail())
    out = tmp_path / "out.jsonl"
    res = fetch_cli.run("http://fake", str(out), records_target=10)
    assert res["ok"] is False
    assert res["code"] == "FETCH_FAILED"
