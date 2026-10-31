# 上下文包 · s1-order-filter

- 任务：订单筛选：按状态、应付金额下限、会员等级与排除名单过滤
- 检索：订单筛选：按状态、应付金额下限、会员等级与排除名单过滤 demo_project/shop/filters.py 的 filter_orders 目前直接返回全部订单，目标测试
demo_project/tests/test_filters.py 失败。需要按规格实现：statuses、min_total_cents、
member_levels、exclude_ids 四个条件；条件之间是 AND；min_total_cents 比较的是折扣后的
应付金额且为「大于等于」；空集合与 None 语义不同；非法枚举抛 ValueError；保持输入顺序。
测试文件是验收依据，不得修改。
 订单筛选 filter_orders 会员等级 应付金额下限 statuses min_total_cents
- spec_digest：`28dfab7c340b059eb13318d452faed82`
- 预算：12000 tokens（估算使用 1193）
- 来源数：12（spec 4 / code 6 / test 2）

## 1. [spec] corpus/specs/S-02-filtering.md:9-10 (hash 8ed7f7727b69c850)

```markdown
## 金额比较
min_total_cents 比较的是订单的应付金额（即 shop.pricing.total_cents(order) 的结果），不是 amount_cents 原价。比较使用大于等于：应付金额 ≥ min_total_cents 即满足条件。这样会员折扣会真实影响筛选结果。
```

## 2. [spec] corpus/specs/S-03-testing.md:12-13 (hash e8ed8afabdf94e87)

```markdown
## 覆盖范围
目标测试覆盖筛选的四类条件、组合条件、空集合与非法值；回归测试覆盖会员折扣与应付金额计算的既有行为。
```

## 3. [spec] corpus/specs/S-02-filtering.md:6-7 (hash 765b4f3a7c542223)

```markdown
## 筛选条件
四个条件全部为可选参数，默认 None 表示该条件不生效。多个条件之间是逻辑与（AND）：一条订单必须同时满足全部已给出的条件才会被返回。statuses 与 member_levels 是字符串集合；min_total_cents 是整数下限；exclude_ids 是要排除的 order_id 集合。
```

## 4. [spec] corpus/specs/S-01-domain.md:12-13 (hash 00bc172d9369d242)

```markdown
## 会员等级与折扣
会员等级只有四个合法取值：regular、silver、gold、platinum，定义在 MEMBER_LEVELS 元组中。对应折扣千分数分别是 0、50、100、150，即 0%、5%、10%、15%。应付金额 = amount_cents × (1000 - 折扣千分数) ÷ 1000，并按 half-up 规则四舍五入到整分。
```

## 5. [code] demo_project/shop/filters.py:11-39 (hash 912e97d1afddfa44)

```python
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
```

## 6. [code] demo_project/shop/pricing.py:1-5 (hash a9f7dca58033fcc5)

```python
"""金额计算：会员折扣与应付总额。"""

from __future__ import annotations

from shop.models import MEMBER_DISCOUNT_PERMILLE, MEMBER_LEVELS, Order
```

## 7. [code] demo_project/shop/models.py:26-35 (hash 5701a7955b2fb26e)

```python
class Order:
    """一条订单。

    amount_cents 是商品原价（单位：分）；应付金额按会员折扣另算，见 shop.pricing。
    """

    order_id: str
    status: str
    member_level: str
    amount_cents: int
```

## 8. [code] demo_project/shop/filters.py:1-9 (hash c411b6a53e939f3d)

```python
"""订单筛选。

目标行为见 corpus/specs/S-02-filtering.md；本文件当前是尚未实现的占位实现。
"""

from __future__ import annotations

from shop.models import Order, STATUSES, MEMBER_LEVELS
from shop.pricing import total_cents
```

## 9. [test] demo_project/tests/test_pricing.py:1-6 (hash cfd9e3f3e1c99f85)

```python
"""回归测试：会员折扣与应付金额的既有行为（改前改后都必须绿）。"""

from __future__ import annotations

from shop.models import Order
from shop.pricing import discount_permille, total_cents
```

## 10. [code] demo_project/shop/pricing.py:15-22 (hash a4540b71bd243cb3)

```python
def total_cents(order: Order) -> int:
    """应付金额（分），四舍五入到整分（half-up）。

    total = amount_cents * (1000 - discount_permille) / 1000
    """
    rate = discount_permille(order.member_level)
    numerator = order.amount_cents * (1000 - rate)
    return (numerator + 500) // 1000
```

## 11. [code] demo_project/shop/models.py:1-25 (hash 0a73ac8acfcf453a)

```python
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
```

## 12. [test] demo_project/tests/test_filters.py:32-33 (hash fdd8e2279c656eb8)

```python
def test_filter_by_status():
    assert _ids(filter_orders(_orders(), statuses={"paid"})) == ["o2", "o4", "o5"]
```
