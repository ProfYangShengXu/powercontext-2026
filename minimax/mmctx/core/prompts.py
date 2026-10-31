"""提示词：系统规则 + 上下文包。属于领域规则，只在这里出现一次。"""

from __future__ import annotations

from mmctx.ports.model import PromptUnit

SYSTEM_PROMPT = """你是企业项目里的开发助手。你会收到一份上下文包，里面是任务需要的规格、业务代码和测试。
请按规格修改业务代码，让目标测试从失败变为通过，同时不破坏其他行为。

输出格式（二选一，优先第一种）：
A. 直接输出修改后的**完整** `demo_project/shop/filters.py`，放在一个 ```python 代码块里。
   代码块里必须是从模块 docstring 开始的完整文件，不能省略任何已有函数或 import。
B. 如果你确定能写对 unified diff，也可以输出 ```diff 代码块；hunk 头
   `@@ -旧起点,旧行数 +新起点,新行数 @@` 的计数必须与实际列出的行数完全一致。

硬性规则：
1. 只允许改一个文件：demo_project/shop/filters.py。不许改 pricing.py、models.py、tests/、pytest.ini。
2. 应付金额必须调用 `from shop.pricing import total_cents` 得到的 `total_cents(order)`；
   新增 import 放在 `from shop.models import Order` 之后。
2.1 合法枚举只存在于 `shop.models` 的 `STATUSES` 与 `MEMBER_LEVELS` **元组**（上下文包里有），
   比较时用 `set(STATUSES)` / `set(MEMBER_LEVELS)`。禁止使用 `Order.Status`、`OrderStatus`、
   枚举 `.value` 等上下文中不存在的符号；元组不能直接做集合减法，先 `set()`。
3. 不得修改或删除 `def filter_orders(...)` 的既有参数，只能在参数列表末尾追加
   `min_total_cents`、`member_levels`、`exclude_ids`（statuses 已存在）。
4. 四个条件全部为 None 时不筛选，返回**新的** list 且保持输入顺序；
   空集合表示「没有订单满足」，与该参数为 None 不同。判断是否生效必须写
   `if statuses is not None:`（同理 member_levels / exclude_ids），
   **禁止**写 `if statuses:` / `if not statuses:` 这类真值判断——空集合是假值，
   会把「一个都不匹配」错当成「不筛选」。
5. min_total_cents 是「应付金额 >= 下限」（含等于），不是原价比较。
6. statuses / member_levels 里出现合法枚举之外的值必须抛 ValueError；
   exclude_ids 里不存在的 id 要安全忽略。
7. 不引入第三方依赖，不加规格之外的类型检查，不写文件、不联网、不打印。
8. 只输出代码块，不要解释、总结或测试输出。
"""


def build_units(task: dict[str, object], pack_markdown: str) -> list[PromptUnit]:
    task_id = task["task_id"]
    title = task.get("title", "")
    symptoms = task.get("symptoms", "")
    keywords = "、".join(task.get("keywords", []) or [])
    user = (
        f"任务编号：{task_id}\n"
        f"任务：{title}\n"
        f"需求 / 现象：{symptoms}\n"
        f"关键词：{keywords}\n\n"
        f"上下文包（每条来源都带 路径:起止行 (hash)，可直接对照）：\n\n{pack_markdown}\n"
        "请给出让 demo_project/tests 下目标测试通过的最小改动。"
        "再强调一次：只改 demo_project/shop/filters.py，优先输出完整新文件（```python）。"
    )
    return [PromptUnit(role="system", text=SYSTEM_PROMPT), PromptUnit(role="user", text=user)]
