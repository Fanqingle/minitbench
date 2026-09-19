"""billing：计费引擎。

对外只暴露 compute_total / render_invoice 两个入口（CLI 用）。
内部实现散落在 legacy / helpers / report 三个模块里。
"""
from .legacy import compute_total, money, region_tax
from .report import render, tax_of

__all__ = ["compute_total", "render_invoice", "money", "region_tax", "tax_of"]


def render_invoice(order, currency="CNY"):
    return render(order, compute_total(order), currency=currency)
