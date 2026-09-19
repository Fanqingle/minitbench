#!/usr/bin/env python3
"""对账批处理入口：读订单 JSON，输出逐单发票与汇总。

被外部对账系统按固定格式消费，输出格式不允许改动。
"""
from __future__ import annotations

import json
import sys

from billing import compute_total, render_invoice


def run(orders_path: str, out_path: str | None = None) -> dict:
    with open(orders_path, encoding="utf-8") as f:
        orders = json.load(f)

    blocks: list[str] = []
    grand = 0.0
    for o in orders:
        total = compute_total(o)
        blocks.append(render_invoice(o))
        grand += float(total["total"])

    blocks.append("=== SUMMARY ===")
    blocks.append(f"orders: {len(orders)}")
    blocks.append(f"grand_total: {grand:.2f}")
    text = "\n".join(blocks) + "\n"

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return {"orders": len(orders), "grand_total": round(grand, 2)}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write("usage: run_billing.py <orders.json> [out.txt]\n")
        return 2
    run(argv[1], argv[2] if len(argv) > 2 else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
