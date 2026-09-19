"""折扣：等级折扣与券折扣。

历史分歧：legacy 用「逐层相乘」，helpers 用「折扣率相加」，
10% + 10% 一个得 0.81 一个得 0.80。这里统一为逐层相乘——
加法叠加会在折扣档位变多时放大误差，且与业务口径不符。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .models import CENT, TIER_DISCOUNT


def tier_rate(tier: str) -> Decimal:
    """会员等级对应的折扣率。未知等级不打折。"""
    return TIER_DISCOUNT.get(tier, Decimal("0.00"))


def clamp_rate(r: float) -> float:
    """把折扣率限制在 [0, 1]：负折扣无意义，超过 100% 会把总额算成负数。"""
    if r < 0:
        return 0.0
    if r > 1:
        return 1.0
    return float(r)


def apply_discounts(net: Decimal, tier_rate: Decimal,
                    coupon_rate: Decimal) -> Decimal:
    """按「逐层相乘」叠加折扣，返回折后净额（四舍五入到分）。"""
    tr = Decimal(str(clamp_rate(float(tier_rate))))
    cr = Decimal(str(clamp_rate(float(coupon_rate))))
    value = Decimal(net) * (Decimal("1") - tr) * (Decimal("1") - cr)
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
