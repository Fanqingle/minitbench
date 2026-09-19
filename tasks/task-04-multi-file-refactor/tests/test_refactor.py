"""Verifier：多文件重构的隐藏验收测试。

验收的不是「代码好不好看」，而是**重构后语义是否被固定住**：
新模块存在、旧入口仍兼容、三条税路径收敛到同一实现、金额走 Decimal、
边界行为明确定义、发票格式未变。

agent 不可见本文件。所有断言都指向「可观测行为」，不检查实现细节
（不禁止任何合法重构路径）。
"""
from __future__ import annotations

import ast
import importlib
from decimal import Decimal

import pytest


def _order(**kw):
    base = {
        "items": [{"sku": "A1", "qty": 2, "unit_price": 10.0}],
        "tier": "bronze",
        "region": "CN",
        "coupon": 0.0,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# A. 结构：目标模块必须存在
# ---------------------------------------------------------------------------
REQUIRED = [
    "billing.models",
    "billing.tax",
    "billing.discount",
    "billing.pricing",
    "billing.report",
]


@pytest.mark.parametrize("mod", REQUIRED)
def test_required_module_exists(mod):
    importlib.import_module(mod)


# ---------------------------------------------------------------------------
# B. 语义收敛：三条税路径必须归口同一实现
# ---------------------------------------------------------------------------
def test_calc_tax_returns_decimal_not_float():
    """金额不能走 float。旧版 round() 与截断版都返回 float，必须收敛到 Decimal。"""
    from billing.tax import calc_tax

    got = calc_tax(Decimal("100"), "CN")
    assert isinstance(got, Decimal), f"calc_tax 必须返回 Decimal，实际 {type(got)}"


def test_calc_tax_is_half_up_on_exact_boundary():
    """38.50 起 13% 税 = 5.005，ROUND_HALF_UP → 5.01（银行家舍入会得 5.00）。"""
    from billing.tax import calc_tax

    assert calc_tax(Decimal("38.50"), "CN") == Decimal("5.01")


def test_calc_tax_unknown_region_is_zero():
    from billing.tax import calc_tax

    assert calc_tax(Decimal("100"), "ZZ") == Decimal("0.00")


def test_legacy_tax_entrypoint_agrees():
    """兼容入口仍可用，且与权威实现口径一致（分歧本身就是要修的 bug）。"""
    from billing.tax import calc_tax

    legacy = importlib.import_module("billing.legacy")
    fn = getattr(legacy, "region_tax", None)
    if fn is None:
        pytest.skip("legacy.region_tax removed — 兼容面已迁移，无需比对")
    ref = calc_tax(Decimal("100"), "CN")
    assert Decimal(str(fn(100, "CN"))) == ref


def test_discount_compounds_multiplicatively():
    """10% 等级 + 10% 券 → 逐层相乘 = 0.9*0.9 = 0.81（相加法是 0.80，是错的）。"""
    from billing.discount import apply_discounts

    got = apply_discounts(Decimal("100"), Decimal("0.10"), Decimal("0.10"))
    assert got == Decimal("81.00"), f"折扣必须逐层相乘，得到 {got}"


def test_coupon_rate_is_clamped():
    """券折扣 > 100% 不得产生负总额。"""
    from billing.discount import clamp_rate

    assert clamp_rate(1.5) == 1.0
    assert clamp_rate(-0.2) == 0.0
    assert clamp_rate(0.25) == 0.25


# ---------------------------------------------------------------------------
# C. 入口兼容：新管线与旧入口必须一致
# ---------------------------------------------------------------------------
def test_pricing_matches_legacy_entrypoint():
    from billing.legacy import compute_total
    from billing.pricing import price_order

    o = _order(tier="gold", coupon=0.05)
    assert price_order(o) == compute_total(o), "新旧入口结果不一致"


def test_pricing_result_shape_and_values():
    from billing.pricing import price_order

    # 2 * 10 = 20；gold 10% → 2；券 0 → net 18；CN 13% → 2.34
    t = price_order(_order(tier="gold", coupon=0.0))
    assert set(t) >= {"subtotal", "discount", "net", "tax", "total"}
    assert t["subtotal"] == 20.0
    assert t["discount"] == 2.0
    assert t["net"] == 18.0
    assert t["tax"] == 2.34
    assert t["total"] == 20.34


# ---------------------------------------------------------------------------
# D. 边界：必须显式定义，而不是静默算成 0
# ---------------------------------------------------------------------------
def test_empty_order_is_rejected():
    from billing.pricing import price_order

    with pytest.raises(ValueError):
        price_order(_order(items=[]))


def test_non_positive_qty_is_rejected():
    from billing.pricing import price_order

    with pytest.raises(ValueError):
        price_order(_order(items=[{"sku": "X", "qty": 0, "unit_price": 1.0}]))


# ---------------------------------------------------------------------------
# E. 类型注解：公开接口必须标注（重构的可读性验收）
# ---------------------------------------------------------------------------
def _annotated(mod_name: str, fn_name: str) -> bool:
    import importlib.util

    spec = importlib.util.find_spec(mod_name)
    if spec is None or not spec.origin:
        return False
    with open(spec.origin, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            a = node.args
            params = [*a.posonlyargs, *a.args, *a.kwonlyargs]
            all_annotated = all(p.annotation is not None for p in params)
            return bool(all_annotated and node.returns is not None)
    return False


@pytest.mark.parametrize("mod,fn", [
    ("billing.tax", "calc_tax"),
    ("billing.discount", "apply_discounts"),
    ("billing.pricing", "price_order"),
])
def test_public_api_is_type_annotated(mod, fn):
    assert _annotated(mod, fn), f"{mod}.{fn} 缺少完整类型注解"


# ---------------------------------------------------------------------------
# F. 不动契约：发票格式逐字保持
# ---------------------------------------------------------------------------
def test_invoice_format_unchanged():
    from billing.report import render

    total = {"subtotal": 20.0, "discount": 2.0, "net": 18.0,
             "tax": 2.34, "total": 20.34}
    text = render(_order(tier="gold"), total)
    lines = text.splitlines()
    assert lines[0] == "=== INVOICE ==="
    assert "currency: CNY" in text
    for label in ("subtotal", "discount", "net", "tax", "total"):
        assert any(l.startswith(f"{label}: ") for l in lines), label
    assert "  - A1 x2" in text
