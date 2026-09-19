"""官方任务的「产物重建评分」（rebuild-from-artifact）本地复现。

为什么需要它
------------
官方 verifier 是**隐藏的**：公开仓库里只有 task.toml 声明的 `artifacts`
文件名和 agent 可见的 harness，评分逻辑（band 映射、加权、门槛）只在服务端。
所以我们能复现的不是"分数"，而是**评分契约**：

    把 agent 产出的产物，放进一个全新的引擎里逐条重放。

对 band 类任务（2048 / sokoban / snake_maze）这恰好也是官方评分逻辑本身——
引擎是确定性的（seed 烘焙进镜像），重放与实时对局 bit-for-bit 一致，因此
「日志无法伪造进度」这件事在本地可以被独立验证，而不需要相信 agent 自报。

这里实现的三个是能从 harness 里取到完整引擎的任务；其余任务（隐藏
verifier + 隐藏 oracle）只导出产物、标记为 server-side，不做本地打分——
这本身是对官方设计的尊重：猜评分逻辑没有意义，能验证的是通道是否可靠。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load(path: Path, name: str):
    """从任意路径加载一个模块（harness 是散装脚本，不在包路径上）。"""
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _iter_tokens(text: str):
    for raw in text.splitlines():
        tok = raw.strip()
        if tok:
            yield tok


# ---------------------------------------------------------------------------
# 2048 —— 最大方块定 band（splitmix64 seed 烘焙进镜像）
# ---------------------------------------------------------------------------
def score_2048(task, moves_text: str) -> dict:
    engine = task.dir / "environment" / "harness" / "game2048_engine.py"
    if not engine.exists():
        return {"error": f"engine not found: {engine}"}
    mod = _load(engine, "official_g2048_engine")
    g = mod.Game2048()
    applied = invalid = 0
    for tok in _iter_tokens(moves_text):
        if tok == "RESET":
            g.reset()
            applied += 1
            continue
        if tok not in mod.DIRS:
            invalid += 1
            continue
        if g.game_over():
            break
        g.move(tok)
        applied += 1
    mx = int(g.max_tile())
    band = int(mod.band_for(mx))
    n = len(mod.MILESTONES)
    return {
        "metric": "max_tile", "value": mx, "band": band, "n_bands": n,
        "score": int(g.score), "normalized_reward": round(band / n, 3),
        "moves_applied": applied, "invalid_tokens": invalid,
        "milestones": mod.MILESTONES,
    }


# ---------------------------------------------------------------------------
# sokoban —— 重放 move log，数解出的关卡数（进展不可伪报）
# ---------------------------------------------------------------------------
def score_sokoban(task, moves_text: str) -> dict:
    h = task.dir / "environment" / "harness"
    try:
        eng = _load(h / "sokoban_engine.py", "official_sokoban_engine")
        lv = _load(h / "levels.py", "official_sokoban_levels")
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}

    levels = lv.LEVELS
    idx, solved, applied, invalid = 0, 0, 0, 0
    g = eng.Sokoban(levels[0])
    for tok in _iter_tokens(moves_text):
        if tok == "RESET":
            idx, solved = 0, 0
            g = eng.Sokoban(levels[0])
            continue
        if tok not in eng.DIRS or g is None:
            invalid += 1
            continue
        g.move(tok)
        applied += 1
        if g.solved():
            solved += 1
            idx += 1
            g = eng.Sokoban(levels[idx]) if idx < len(levels) else None
    return {
        "metric": "levels_solved", "value": solved, "n_levels": len(levels),
        "moves_applied": applied, "invalid_tokens": invalid,
        "normalized_reward": round(solved / len(levels), 3),
    }


# ---------------------------------------------------------------------------
# snake_maze_campaign —— 重放，按吃掉的食物数定 band
# ---------------------------------------------------------------------------
def score_snake_maze(task, moves_text: str) -> dict:
    h = task.dir / "environment" / "harness"
    try:
        eng = _load(h / "snake_engine.py", "official_snake_engine")
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}

    g = eng.SnakeGame()
    applied = invalid = 0
    for tok in _iter_tokens(moves_text):
        if tok == "RESET":
            g.reset()
            continue
        if tok not in eng.DIRS:
            invalid += 1
            continue
        r = g.step(tok)
        applied += 1
        if getattr(r, "dead", False) or getattr(r, "game_over", False):
            break
    m = g.metrics()
    foods = int(m.get("foods", 0))
    return {
        "metric": "foods", "value": foods,
        "band": int(eng.band_for_foods(foods)),
        "n_bands": len(eng.MILESTONES),
        "normalized_reward": round(float(eng.value_for_foods(foods)), 3),
        "moves_applied": applied, "invalid_tokens": invalid,
        "milestones": eng.MILESTONES,
    }


# ---------------------------------------------------------------------------
_BUILDERS = {
    "2048": score_2048,
    "sokoban": score_sokoban,
    "snake_maze_campaign": score_snake_maze,
}


def rebuild_score(task, artifact_name: str, text: str) -> dict | None:
    """按任务分发重建评分。返回 None 表示该任务评分只在服务端（隐藏 verifier）。"""
    fn = _BUILDERS.get(task.slug)
    if fn is None:
        return None
    try:
        return fn(task, text)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def has_local_rebuild(slug: str) -> bool:
    return slug in _BUILDERS


LOCAL_REBUILD_SLUGS = frozenset(_BUILDERS)
