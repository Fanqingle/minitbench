"""历史工具模块——重构后只保留转发，不再有独立实现。

旧版这里是税计算与折扣的第二份拷贝（Decimal 舍入 + 折扣率相加），
是「同一订单两个结果」的直接来源。
"""
from __future__ import annotations

from decimal import Decimal

from .discount import apply_discounts as _apply_discounts
from .discount import clamp_rate as _clamp_rate
from .models import REGION_TAX
from .tax import calc_tax

__all__ = ["money", "tax_amount", "apply_discounts", "clamp_rate", "REGION_TAX"]


def money(v: float) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


def tax_amount(value: float, region: str) -> float:
    """历史入口：转发到 calc_tax。"""
    return float(calc_tax(Decimal(str(value)), region))


def apply_discounts(value: float, tier_rate: float, coupon_rate: float) -> float:
    """历史入口：转发到权威实现（旧版此处把折扣率相加，语义是错的）。"""
    return float(_apply_discounts(Decimal(str(value)),
                                  Decimal(str(tier_rate)),
                                  Decimal(str(coupon_rate))))


def clamp_rate(r: float) -> float:
    return _clamp_rate(r)
