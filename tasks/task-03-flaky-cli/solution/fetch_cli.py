"""Oracle 解法：超时 + 指数退避重试 + 幂等去重 + 部分成功语义。"""
import json
import os
import random
import time
import urllib.request
import urllib.error

RETRY_MAX = int(os.environ.get("RETRY_MAX", "4"))
TIMEOUT = float(os.environ.get("FETCH_TIMEOUT", "3"))
BACKOFF = [0.5, 1, 2, 4]
JITTER = float(os.environ.get("BACKOFF_JITTER", "0.2"))


def get(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode()


def _write(seen: dict, out_path: str) -> None:
    with open(out_path, "w", encoding="utf-8") as f:
        for rec in seen.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run(url: str, out_path: str, records_target: int = 10):
    seen = {}
    attempts = 0
    max_attempts = 1 + RETRY_MAX
    while len(seen) < records_target:
        try:
            text = get(url)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            attempts += 1
            if attempts >= max_attempts:
                # 部分成功语义：保留已完成部分，返回明确错误码
                _write(seen, out_path)
                return {"ok": False, "code": "FETCH_FAILED", "got": len(seen)}
            jitter = random.uniform(-JITTER, JITTER) * BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
            time.sleep(max(0.0, BACKOFF[min(attempts - 1, len(BACKOFF) - 1)] + jitter))
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            seen[rec["id"]] = rec          # 幂等：相同 id 覆盖
    _write(seen, out_path)
    return {"ok": True, "code": "OK", "got": len(seen)}
