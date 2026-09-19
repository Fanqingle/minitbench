"""订单定价主流程——唯一入口。"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .discount import apply_discounts, clamp_rate, tier_rate
from .models import CENT, Order
from .tax import calc_tax


def _to_order(raw: dict | Order) -> Order:
    return raw if isinstance(raw, Order) else Order.from_dict(raw)


def _validate(order: Order) -> None:
    """边界显式化。

    旧实现遇到空订单或 qty<=0 会静默算成 0，财务对账时才发现对不上——
    静默的零比明确报错危险得多。
    """
    if not order.items:
        raise ValueError("order must contain at least one item")
    for it in order.items:
        if it.qty <= 0:
            raise ValueError(f"qty must be positive, got {it.qty} for sku={it.sku!r}")


def price_order(order: dict | Order) -> dict:
    """计算订单金额：小计 → 折扣 → 税 → 总额。

    返回 float（保留 2 位小数），以兼容下游对账系统的既有解析。
    """
    o = _to_order(order)
    _validate(o)

    subtotal = sum((it.unit_price * it.qty for it in o.items), Decimal("0"))
    subtotal = subtotal.quantize(CENT, rounding=ROUND_HALF_UP)

    cr = Decimal(str(clamp_rate(float(o.coupon))))
    net = apply_discounts(subtotal, tier_rate(o.tier), cr)
    discount = (subtotal - net).quantize(CENT, rounding=ROUND_HALF_UP)
    tax = calc_tax(net, o.region)
    total = (net + tax).quantize(CENT, rounding=ROUND_HALF_UP)

    return {
        "subtotal": float(subtotal),
        "discount": float(discount),
        "net": float(net),
        "tax": float(tax),
        "total": float(total),
    }
