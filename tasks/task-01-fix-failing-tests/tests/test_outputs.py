"""Verifier 行为验收测试（agent 不可见）。

反作弊检查（tests-untouched / no-hardcode / no-trivial-pass）由 harness 的
verifier.anti_cheat 在 Python 层统一执行（见 mini_tbench/verifier.py），
本文件只负责业务语义断言，保持可移植（本地与 Docker 行为一致）。
"""
import pytest


@pytest.fixture
def svc():
    from app import InventoryService
    return InventoryService(stock={"SKU-001": 10, "SKU-002": 3})


def test_rejects_invalid_qty(svc):
    with pytest.raises(ValueError):
        svc.deduct("SKU-001", 0, "o1")
    with pytest.raises(ValueError):
        svc.deduct("SKU-001", -5, "o2")
    assert svc.stock["SKU-001"] == 10, "非法扣减不应改变库存"


def test_idempotent_retry(svc):
    assert svc.deduct("SKU-001", 4, "order-A") is True
    assert svc.deduct("SKU-001", 4, "order-A") is True   # 同订单重试 → 幂等
    assert svc.stock["SKU-001"] == 6, "重试不得重复扣减"
    assert svc.deduct("SKU-001", 4, "order-B") is True   # 不同订单正常扣
    assert svc.stock["SKU-001"] == 2


def test_pieces_unit_conversion(svc):
    assert svc.deduct_pieces("SKU-002", 25, "order-C") is True   # 25 片 = 3 盒
    assert svc.stock["SKU-002"] == 0
    assert svc.deduct_pieces("SKU-002", 1, "order-D") is False   # 已无库存


def test_insufficient_stock(svc):
    assert svc.deduct("SKU-001", 11, "o3") is False
    assert svc.stock["SKU-001"] == 10
