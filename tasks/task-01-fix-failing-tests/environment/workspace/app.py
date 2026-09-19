"""库存扣减服务（含 3 个待修复 bug）。

业务语义（预期行为，已由验收方确认）：
1. 扣减数量必须为正整数；负数或 0 抛 ValueError。
2. 同一订单号重复调用 deduct 属于重试，必须幂等（第二次调用不重复扣减，返回 True）。
3. 库存数量单位是「整盒」，外部传入单位可能是「片」，每盒 10 片，需换算后扣减；
   换算后不足 1 盒向上取整（宁可多扣不可超卖）。
"""

from dataclasses import dataclass, field


@dataclass
class InventoryService:
    stock: dict = field(default_factory=dict)          # sku -> 剩余盒数
    deducted_orders: set = field(default_factory=set)  # 已扣减的订单号

    def deduct(self, sku: str, qty: int, order_id: str) -> bool:
        """扣减库存。成功返回 True；库存不足返回 False。"""
        # BUG 1: 未校验 qty 合法性，负数会凭空增加库存
        if self.stock.get(sku, 0) < qty:
            return False
        # BUG 2: 幂等未实现，同一订单重试会重复扣减
        self.stock[sku] = self.stock.get(sku, 0) - qty
        return True

    def deduct_pieces(self, sku: str, pieces: int, order_id: str) -> bool:
        """按「片」扣减（每盒 10 片）。"""
        # BUG 3: 未做单位换算，直接把片当盒扣，导致少卖/超卖
        return self.deduct(sku, pieces, order_id)
