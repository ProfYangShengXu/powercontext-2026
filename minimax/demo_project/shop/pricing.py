"""金额计算：会员折扣与应付总额。"""

from __future__ import annotations

from shop.models import MEMBER_DISCOUNT_PERMILLE, MEMBER_LEVELS, Order


def discount_permille(member_level: str) -> int:
    """返回会员等级对应的折扣千分数；未知等级抛 ValueError。"""
    if member_level not in MEMBER_DISCOUNT_PERMILLE:
        raise ValueError(f"unknown member level: {member_level!r}")
    return MEMBER_DISCOUNT_PERMILLE[member_level]


def total_cents(order: Order) -> int:
    """应付金额（分），四舍五入到整分（half-up）。

    total = amount_cents * (1000 - discount_permille) / 1000
    """
    rate = discount_permille(order.member_level)
    numerator = order.amount_cents * (1000 - rate)
    return (numerator + 500) // 1000
