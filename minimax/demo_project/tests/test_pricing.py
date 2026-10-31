"""回归测试：会员折扣与应付金额的既有行为（改前改后都必须绿）。"""

from __future__ import annotations

from shop.models import Order
from shop.pricing import discount_permille, total_cents


def test_discount_permille_by_level():
    assert discount_permille("regular") == 0
    assert discount_permille("silver") == 50
    assert discount_permille("gold") == 100
    assert discount_permille("platinum") == 150


def test_total_cents_with_member_discount():
    assert total_cents(Order("x", "paid", "regular", 9000)) == 9000
    assert total_cents(Order("x", "paid", "silver", 8000)) == 7600
    assert total_cents(Order("x", "paid", "gold", 12000)) == 10800
    assert total_cents(Order("x", "paid", "platinum", 10000)) == 8500


def test_total_cents_rounds_half_up():
    assert total_cents(Order("x", "paid", "silver", 999)) == 949
    assert total_cents(Order("x", "paid", "silver", 10)) == 10


def test_unknown_member_level_raises():
    import pytest

    with pytest.raises(ValueError):
        total_cents(Order("x", "paid", "diamond", 100))
