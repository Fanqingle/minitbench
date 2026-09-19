"""发票渲染。

渲染格式是**对外契约**（外部对账系统按固定字段解析），重构不得改动。
本模块不再自行计算税——历史上它带了一份截断版实现，是分歧来源之一。
"""
from __future__ import annotations

from decimal import Decimal

from .models import Order
from .tax import calc_tax


def tax_of(value: float, region: str) -> Decimal:
    """兼容旧签名：转发到权威实现（旧版此处用 int() 截断，会少算税）。"""
    return calc_tax(Decimal(str(value)), region)


def render(order: dict | Order, total: dict, currency: str = "CNY") -> str:
    """渲染发票文本。字段顺序与格式保持与原实现逐字一致。"""
    raw_items = order.items if isinstance(order, Order) else (order.get("items") or [])
    lines = [
        "=== INVOICE ===",
        f"currency: {currency}",
        f"subtotal: {total['subtotal']:.2f}",
        f"discount: {total['discount']:.2f}",
        f"net: {total['net']:.2f}",
        f"tax: {total['tax']:.2f}",
        f"total: {total['total']:.2f}",
    ]
    for it in raw_items:
        sku = it.sku if hasattr(it, "sku") else it["sku"]
        qty = it.qty if hasattr(it, "qty") else it["qty"]
        lines.append(f"  - {sku} x{qty}")
    return "\n".join(lines)
