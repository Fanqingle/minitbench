# 销售数据聚合口径定义（验收基线，agent 不得修改本文件）

输入 `sales.csv` 列：`order_id, store, date, amount, type`

- **去重键**：`order_id`。同 order_id 出现多行只计一次，取首次出现的金额。
- **缺失金额**：`amount` 为空时视为 `0`，不计入总额但计入订单数。
- **退款语义**：`type=refund` 的行，其 `amount` 为退款额，应从该门店总额中**扣除**
  （`amount` 为负表示冲正，同样扣除负值即加回）。
- **输出**：`summary.json`，结构 `{"stores": {store: {"total": float, "orders": int}}}`，
  `total` 保留两位小数；相同输入两次运行输出须逐字节一致（确定性）。
