"""发票渲染（税计算在这里被复制了第三份，而且用的是截断）。

第三份不是有意写的——是当时为了不改动 legacy 的调用方，直接复制粘贴后
「顺手简化」了舍入，于是它系统性地少算税。
"""
REGION_TAX = {"CN": 0.13, "US": 0.08, "EU": 0.20}


def tax_of(value, region):
    """税——第三份实现。int(x*100)/100 是截断，不是四舍五入。"""
    rate = REGION_TAX.get(region, 0.0)
    return int(value * rate * 100) / 100.0


def render(order, total, currency="CNY"):
    """发票文本。格式被外部对账系统依赖，不得更改。"""
    lines = [
        "=== INVOICE ===",
        f"currency: {currency}",
        f"subtotal: {total['subtotal']:.2f}",
        f"discount: {total['discount']:.2f}",
        f"net: {total['net']:.2f}",
        f"tax: {total['tax']:.2f}",
        f"total: {total['total']:.2f}",
    ]
    for it in order.get("items", []):
        lines.append(f"  - {it['sku']} x{it['qty']}")
    return "\n".join(lines)
