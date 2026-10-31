"""订单领域模型：字段、状态与会员等级。"""

from __future__ import annotations

from dataclasses import dataclass

STATUS_PENDING = "pending"
STATUS_PAID = "paid"
STATUS_SHIPPED = "shipped"
STATUS_CANCELLED = "cancelled"

STATUSES = (STATUS_PENDING, STATUS_PAID, STATUS_SHIPPED, STATUS_CANCELLED)

MEMBER_LEVELS = ("regular", "silver", "gold", "platinum")

# 折扣率用千分数表示，避免浮点误差：50 表示 5%。
MEMBER_DISCOUNT_PERMILLE = {
    "regular": 0,
    "silver": 50,
    "gold": 100,
    "platinum": 150,
}


@dataclass(frozen=True)
class Order:
    """一条订单。

    amount_cents 是商品原价（单位：分）；应付金额按会员折扣另算，见 shop.pricing。
    """

    order_id: str
    status: str
    member_level: str
    amount_cents: int
