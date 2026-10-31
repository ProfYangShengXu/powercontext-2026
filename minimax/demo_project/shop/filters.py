"""订单筛选。

目标行为见 corpus/specs/S-02-filtering.md；本文件当前是尚未实现的占位实现。
"""

from __future__ import annotations

from shop.models import Order, STATUSES, MEMBER_LEVELS
from shop.pricing import total_cents

def filter_orders(
    orders: list[Order],
    *,
    statuses: set[str] | None = None,
    min_total_cents: int | None = None,
    member_levels: set[str] | None = None,
    exclude_ids: set[str] | None = None,
) -> list[Order]:
    """按状态、应付金额下限、会员等级与排除名单筛选订单，保持输入顺序。"""
    if statuses is not None:
        if not statuses.issubset(set(STATUSES)):
            raise ValueError(f"Invalid statuses: {statuses - set(STATUSES)}")
    if member_levels is not None:
        if not member_levels.issubset(set(MEMBER_LEVELS)):
            raise ValueError(f"Invalid member_levels: {member_levels - set(MEMBER_LEVELS)}")
    
    filtered_orders = []
    for order in orders:
        if statuses is not None and order.status not in statuses:
            continue
        if min_total_cents is not None and total_cents(order) < min_total_cents:
            continue
        if member_levels is not None and order.member_level not in member_levels:
            continue
        if exclude_ids is not None and order.order_id in exclude_ids:
            continue
        filtered_orders.append(order)
    
    return filtered_orders
