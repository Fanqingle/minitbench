"""Oracle 解法：用于验证 verifier 本身的正确性（先跑 oracle，verifier 必须全绿）。"""
from dataclasses import dataclass, field


@dataclass
class InventoryService:
    stock: dict = field(default_factory=dict)
    deducted_orders: set = field(default_factory=set)

    def deduct(self, sku: str, qty: int, order_id: str) -> bool:
        if not isinstance(qty, int) or qty <= 0:
            raise ValueError("qty 必须为正整数")
        if order_id in self.deducted_orders:      # 幂等：重试不重复扣
            return True
        if self.stock.get(sku, 0) < qty:
            return False
        self.stock[sku] = self.stock.get(sku, 0) - qty
        self.deducted_orders.add(order_id)
        return True

    def deduct_pieces(self, sku: str, pieces: int, order_id: str) -> bool:
        if not isinstance(pieces, int) or pieces <= 0:
            raise ValueError("pieces 必须为正整数")
        boxes = -(-pieces // 10)                  # 向上取整
        return self.deduct(sku, boxes, order_id)
