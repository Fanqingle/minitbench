"""数据模型与常量。"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

TIER_DISCOUNT: dict[str, Decimal] = {
    "bronze": Decimal("0.00"),
    "silver": Decimal("0.05"),
    "gold": Decimal("0.10"),
}

REGION_TAX: dict[str, Decimal] = {
    "CN": Decimal("0.13"),
    "US": Decimal("0.08"),
    "EU": Decimal("0.20"),
}

CENT = Decimal("0.01")


@dataclass(frozen=True)
class LineItem:
    sku: str
    qty: int
    unit_price: Decimal

    @classmethod
    def from_dict(cls, raw: dict) -> "LineItem":
        return cls(
            sku=str(raw["sku"]),
            qty=int(raw["qty"]),
            unit_price=Decimal(str(raw["unit_price"])),
        )


@dataclass(frozen=True)
class Order:
    items: list[LineItem]
    tier: str = "bronze"
    region: str = "CN"
    coupon: Decimal = Decimal("0.00")

    @classmethod
    def from_dict(cls, raw: dict) -> "Order":
        return cls(
            items=[LineItem.from_dict(i) for i in (raw.get("items") or [])],
            tier=str(raw.get("tier") or "bronze"),
            region=str(raw.get("region") or "CN"),
            coupon=Decimal(str(raw.get("coupon") or 0)),
        )
