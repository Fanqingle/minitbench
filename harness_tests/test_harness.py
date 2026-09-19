"""harness 自身的单元测试（不是任务 verifier）。

确保「评测器」本身正确：reward 计算、去重、gaming 判定、哈希稳定性。
运行：python -m pytest harness_tests -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mini_tbench.dedup import DedupIndex, normalize_diff
from mini_tbench.gaming import GamingAnalyzer
from mini_tbench.reward import compute_reward
from mini_tbench.verifier import sha256_of_dir


def test_reward_pass_full():
    r = compute_reward("PASS", 4, 4, False, "none", True)
    assert r.pass_reward == 1.0 and r.total > 1.0 - 1e-9
    assert r.total <= 1.0


def test_reward_gaming_negative():
    r = compute_reward("FAIL", 0, 4, False, "gaming", False)
    assert r.gaming_penalty == -1.0 and r.total < 0


def test_reward_partial_credit():
    r = compute_reward("FAIL", 2, 4, False, "logic_error", True)
    assert 0 < r.partial_reward < 1.0


def test_dedup_exact_and_near():
    idx = DedupIndex(threshold=0.9)
    assert idx.add("r1", "PASS", "~ app.py\n+ new") is True
    assert idx.add("r2", "PASS", "~ app.py\n+ new") is False          # 精确重复
    assert idx.add("r3", "PASS", "~ app.py\n+ new  ") is False        # 近似重复
    assert idx.add("r4", "FAIL", "completely different diff") is True  # 不同


def test_normalize_diff_strips_comment_ws():
    assert normalize_diff("# hi\n  A  B ") == "a b"


def test_gaming_detects_hardcode_from_checks():
    class T:
        run_id = "r"
        task = "t"
        diff = ""
        verdict = "FAIL"
        failure_tag = "gaming"
        meta = {"checks": [{"id": "no-hardcode", "passed": False},
                           {"id": "no-trivial-pass", "passed": True}]}
    rep = GamingAnalyzer().analyze(T())
    assert rep.flagged and rep.should_drop and "hardcode" in rep.signals


def test_sha256_dir_stable(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    (d / "a.py").write_text("print(1)")
    h1 = sha256_of_dir(d)
    h2 = sha256_of_dir(d)
    assert h1 == h2
    (d / "a.py").write_text("print(2)")
    assert sha256_of_dir(d) != h1
