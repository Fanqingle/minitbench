"""工具函数（税计算在这里被复制了第二份）。

这份用的是 Decimal + ROUND_HALF_UP，比 legacy 的 round() 正确，
但两个入口都在被调用，导致同一张订单走不同路径会差几分钱。
"""
from decimal import Decimal, ROUND_HALF_UP

REGION_TAX = {"CN": 0.13, "US": 0.08, "EU": 0.20}


def money(v):
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def tax_amount(value, region):
    """税——第二份实现。与 legacy.region_tax 的分歧点：舍入方式不同。"""
    rate = REGION_TAX.get(region, 0.0)
    return float(money(Decimal(str(value)) * Decimal(str(rate))))


def apply_discounts(value, tier_rate, coupon_rate):
    """折扣——与 legacy.compute_total 的分歧点：这里是「折扣率直接相加」。

    10% + 10% 在这里等于打 8 折；在 legacy 里等于打 8.1 折。差一个点。
    """
    return value * (1 - (tier_rate + coupon_rate))


def clamp_rate(r):
    if r < 0:
        return 0.0
    if r > 1:
        return 1.0
    return r
