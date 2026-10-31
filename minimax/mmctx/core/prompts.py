"""提示词：系统规则 + 上下文包。属于领域规则，只在这里出现一次。"""

from __future__ import annotations

from mmctx.ports.model import PromptUnit

SYSTEM_PROMPT = """你是企业项目里的开发助手。你会收到一份上下文包，里面是任务需要的规格、业务代码和测试。
请按规格修改业务代码，让目标测试从失败变为通过，同时不破坏其他行为。

硬性规则：
1. 只输出一个 unified diff，放在 ```diff 代码块里，不要输出解释、总结或多余文字。
2. 补丁路径必须写成 a/demo_project/shop/<file>.py 与 b/demo_project/shop/<file>.py。
3. 绝对不许修改 tests/、pytest.ini、conftest.py 或任何测试文件；改测试视为失败。
4. 不要新增第三方依赖；只用 Python 标准库与项目已有模块。
5. 补丁必须能被 git apply 直接应用：包含 @@ hunk 头与足够的上文/下文行。
6. 严格按规格实现边界语义（例如「大于等于」「空集合与 None 不同」「非法值抛 ValueError」）。
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
        "请给出让 demo_project/tests 下目标测试通过的最小补丁。"
    )
    return [PromptUnit(role="system", text=SYSTEM_PROMPT), PromptUnit(role="user", text=user)]
