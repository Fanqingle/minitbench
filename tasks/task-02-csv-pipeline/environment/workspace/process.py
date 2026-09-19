"""销售数据聚合管道（含待修复缺陷，agent 需修复）。

口径定义见同目录 README.md。正确实现应：按 order_id 去重、缺失金额当 0、
退款额从门店总额扣除、输出确定性 summary.json。
"""


def summarize(rows):
    stores = {}
    for r in rows:
        store = r["store"]
        oid = r["order_id"]
        # BUG1: 未做 order_id 去重，重复订单被重复计入
        # BUG2: 缺失金额直接 float() 会崩溃（应视为 0）
        amt = float(r["amount"])
        typ = r.get("type", "")
        s = stores.setdefault(store, {"total": 0.0, "orders": 0})
        if typ == "refund":
            pass  # BUG3: 退款未从总额扣除
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
