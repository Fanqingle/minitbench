"""Oracle 解法：正确实现聚合口径（用于校验 verifier 本身）。"""


def summarize(rows):
    stores = {}
    seen = set()
    for r in rows:
        store = (r.get("store") or "").strip()
        if not store:
            continue
        oid = (r.get("order_id") or "").strip()
        if oid in seen:
            continue  # 去重键 order_id
        seen.add(oid)
        try:
            amt = float((r.get("amount") or "").strip() or 0)  # 缺失金额 → 0
        except ValueError:
            amt = 0.0
        typ = (r.get("type") or "").strip().lower()
        s = stores.setdefault(store, {"total": 0.0, "orders": 0})
        if typ == "refund":
            s["total"] -= amt  # 退款扣除（负额即冲正加回）
        else:
            s["total"] += amt
        s["orders"] += 1
    return {
        "stores": {
            k: {"total": round(v["total"], 2), "orders": v["orders"]}
            for k, v in stores.items()
        }
    }


def main(input_csv="sales.csv", output_json="summary.json"):
    import csv
    import json

    with open(input_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = summarize(rows)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


if __name__ == "__main__":
    main()
