"""冒烟测试：只覆盖常规路径（边界与异常不在这里）。

注意这些测试通过就代表「没改坏常见情况」，不代表实现是对的。
"""
from billing import compute_total, render_invoice


def test_compute_total_basic():
    order = {
        "items": [{"sku": "A1", "qty": 2, "unit_price": 10.0}],
        "tier": "bronze",
        "region": "CN",
        "coupon": 0.0,
    }
    t = compute_total(order)
    assert t["subtotal"] == 20.0
    assert set(t) >= {"subtotal", "discount", "net", "tax", "total"}


def test_invoice_has_expected_lines():
    order = {
        "items": [{"sku": "B2", "qty": 1, "unit_price": 5.0}],
        "tier": "silver",
        "region": "US",
        "coupon": 0.0,
    }
    text = render_invoice(order)
    for label in ("subtotal", "discount", "net", "tax", "total"):
        assert f"{label}:" in text
    assert "B2 x1" in text
