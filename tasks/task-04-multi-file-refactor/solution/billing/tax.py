"""税计算——全工程唯一实现。

历史上这里有三份拷贝（legacy.region_tax / helpers.tax_amount /
report.tax_of），分别用了银行家舍入、Decimal 舍入、和截断，导致同一张订单
走不同路径差几分钱。现在统一到这里。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .models import CENT, REGION_TAX


def calc_tax(net: Decimal, region: str) -> Decimal:
    """按地区税率计算税额，四舍五入到分（ROUND_HALF_UP）。

    金额一律走 Decimal：float 的二进制表示会让 5.005 这类边界值随机偏向
    一侧，而财务对账要求可复现。
    """
    rate = REGION_TAX.get(region, Decimal("0.00"))
    amount = Decimal(net) * rate
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)
