"""billing：订单计费引擎。

模块职责：
    models    数据模型与税率/折扣常量
    tax       唯一的税计算实现（Decimal + ROUND_HALF_UP）
    discount  等级折扣与券折扣（逐层相乘）
    pricing   定价主流程（唯一计算入口）
    report    发票渲染（对外格式契约）
    legacy    历史入口的兼容转发层
"""
from __future__ import annotations

from .legacy import compute_total, money, region_tax
from .pricing import price_order
from .report import render, tax_of

__all__ = [
    "compute_total", "price_order", "render_invoice",
    "money", "region_tax", "tax_of",
]


def render_invoice(order: dict, currency: str = "CNY") -> str:
    return render(order, compute_total(order), currency=currency)
