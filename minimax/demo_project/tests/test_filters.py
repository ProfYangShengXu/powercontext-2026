"""目标测试：订单筛选的规格行为（改前失败，改后必须全绿）。"""

from __future__ import annotations

import pytest

from shop.filters import filter_orders
from shop.models import Order


def _orders() -> list[Order]:
    return [
        Order("o1", "pending", "regular", 9000),
        Order("o2", "paid", "gold", 12000),
        Order("o3", "shipped", "silver", 8000),
        Order("o4", "paid", "platinum", 10000),
        Order("o5", "paid", "gold", 5000),
    ]


def _ids(orders: list[Order]) -> list[str]:
    return [order.order_id for order in orders]


def test_defaults_return_everything_in_input_order():
    orders = _orders()
    result = filter_orders(orders)
    assert _ids(result) == ["o1", "o2", "o3", "o4", "o5"]
    assert result is not orders


def test_filter_by_status():
    assert _ids(filter_orders(_orders(), statuses={"paid"})) == ["o2", "o4", "o5"]


def test_min_total_uses_discounted_total():
    # o1 9000>=9000；o2 应付 10800；o4 原价 10000 但应付 8500，应被排除。
    assert _ids(filter_orders(_orders(), min_total_cents=9000)) == ["o1", "o2"]


def test_min_total_is_inclusive():
    assert _ids(filter_orders(_orders(), statuses={"paid"}, min_total_cents=8500)) == ["o2", "o4"]


def test_filter_by_member_levels():
    assert _ids(filter_orders(_orders(), member_levels={"silver", "platinum"})) == ["o3", "o4"]


def test_filters_combine_with_and():
    result = filter_orders(_orders(), statuses={"paid"}, min_total_cents=9000, exclude_ids={"o2"})
    assert result == []


def test_exclude_ids():
    assert _ids(filter_orders(_orders(), statuses={"paid"}, exclude_ids={"o2"})) == ["o4", "o5"]


def test_empty_set_matches_nothing():
    assert filter_orders(_orders(), statuses=set()) == []


def test_unknown_status_raises_value_error():
    with pytest.raises(ValueError):
        filter_orders(_orders(), statuses={"refunded"})


def test_unknown_member_level_filter_raises_value_error():
    with pytest.raises(ValueError):
        filter_orders(_orders(), member_levels={"vip"})


def test_exclude_unknown_id_is_ignored():
    assert _ids(filter_orders(_orders(), exclude_ids={"does-not-exist"})) == ["o1", "o2", "o3", "o4", "o5"]
