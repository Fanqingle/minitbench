"""数据拉取 CLI（含待修复缺陷，agent 需增加超时/重试/幂等）。

正确行为：单请求超时 3s、指数退避重试（≤4 次，0.5/1/2/4 ±20% 抖动）、
按记录 id 幂等去重（响应可能重复投递）、最终失败保留已完成部分并返回错误码、
重试参数可由环境变量覆盖。
"""
import json
import os
import time
import urllib.request
import urllib.error

RETRY_MAX = int(os.environ.get("RETRY_MAX", "4"))
TIMEOUT = float(os.environ.get("FETCH_TIMEOUT", "30"))   # BUG: 未用 3s 超时
BACKOFF = [0.5, 1, 2, 4]
JITTER = 0.2


def get(url):
    # BUG: 无超时、无重试
    with urllib.request.urlopen(url) as r:
        return r.read().decode()


def _write(seen: dict, out_path: str) -> None:
    with open(out_path, "w", encoding="utf-8") as f:
        for rec in seen.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run(url: str, out_path: str, records_target: int = 10):
    seen = {}
    # BUG: 无重试，单次失败直接崩溃，无幂等去重
    text = get(url)
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        seen[rec["id"]] = rec
    _write(seen, out_path)
    return {"ok": True, "code": "OK"}


if __name__ == "__main__":
    import sys
    run(sys.argv[1], sys.argv[2])
