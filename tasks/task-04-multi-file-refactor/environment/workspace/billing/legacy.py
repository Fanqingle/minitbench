"""计费引擎（历史遗留：所有逻辑集中在这一个模块里）。

最初只有这一份实现。为了赶上线，税和折扣的片段被复制到了 helpers.py 与
report.py，三份实现随后各自被人改过，现在结果已经不一致了。没人敢删。
"""
from decimal import Decimal, ROUND_HALF_UP

TIER_DISCOUNT = {"bronze": 0.0, "silver": 0.05, "gold": 0.10}
REGION_TAX = {"CN": 0.13, "US": 0.08, "EU": 0.20}


def subtotal(items):
    total = 0.0
    for it in items:
        total += it["qty"] * it["unit_price"]
    return total


def tier_discount(value, tier):
    return value * TIER_DISCOUNT.get(tier, 0.0)


def coupon_discount(value, coupon_rate):
    return value * coupon_rate


def region_tax(value, region):
    """税——本模块的版本。注意：这里用的是 round()（银行家舍入）。"""
    rate = REGION_TAX.get(region, 0.0)
    return round(value * rate, 2)


def compute_total(order):
    """订单总价。折扣按「逐层相乘」处理：先减等级折扣，再减券。"""
    st = subtotal(order.get("items", []))
    d1 = tier_discount(st, order.get("tier"))
    after_tier = st - d1
    d2 = coupon_discount(after_tier, order.get("coupon", 0.0))
    net = after_tier - d2
    tax = region_tax(net, order.get("region"))
    return {
        "subtotal": round(st, 2),
        "discount": round(d1 + d2, 2),
        "net": round(net, 2),
        "tax": tax,
        "total": round(net + tax, 2),
    }


def money(v):
    """金额格式化（helpers.money 是它的第二份拷贝）。"""
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
