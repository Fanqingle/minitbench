"""兼容层。

`billing/legacy.py` 曾经是所有逻辑的实现体。重构后这里只剩转发——
老调用方（`billing.compute_total`、`legacy.region_tax`、`legacy.money`）
继续可用，但计算口径已经统一到 tax / discount / pricing 三个权威模块，
不会再出现「同一张订单两个结果」。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .models import TIER_DISCOUNT
from .pricing import price_order
from .tax import calc_tax


def compute_total(order: dict) -> dict:
    """历史入口：转发到 price_order。"""
    return price_order(order)


def region_tax(value: float, region: str) -> Decimal:
    """历史入口：转发到 calc_tax（旧版用 round()，与权威口径不同）。"""
    return calc_tax(Decimal(str(value)), region)


def money(v: float) -> Decimal:
    """历史入口：金额量化到分。"""
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# 历史常量仍然可读，但取值以 models 为准
TIER_DISCOUNT = TIER_DISCOUNT
