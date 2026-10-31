"""订单筛选。

目标行为见 corpus/specs/S-02-filtering.md；本文件当前是尚未实现的占位实现。
"""

from __future__ import annotations

from shop.models import Order


def filter_orders(
    orders: list[Order],
    *,
    statuses: set[str] | None = None,
    min_total_cents: int | None = None,
    member_levels: set[str] | None = None,
    exclude_ids: set[str] | None = None,
) -> list[Order]:
    """按状态、应付金额下限、会员等级筛选订单，保持输入顺序。"""
    return list(orders)
