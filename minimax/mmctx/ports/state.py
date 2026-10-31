"""StatePort：存任务状态并跨重启复用（本项目由 PowerContext Basic Memory 实现）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class StateTooLargeError(Exception):
    """状态文本超过实现上限。

    这是 PowerContext 的实现约束，校验发生在适配器内部；端口签名里不出现
    「字节数」参数，core 也不需要知道 8192。
    """

    def __init__(self, size: int, limit: int) -> None:
        super().__init__(f"state text is {size} UTF-8 bytes, limit is {limit}")
        self.size = size
        self.limit = limit


@dataclass(frozen=True)
class StateRef:
    """一次状态写入的引用。"""

    entry_id: str
    version: int
    kind: str
    unchanged: bool = False


@dataclass(frozen=True)
class StateRecord:
    """读回的一条状态：原文 + 引用。"""

    text: str
    ref: StateRef
    seq: int = 0


class StatePort(Protocol):
    """任务状态的保存与读回。"""

    def save(self, task_id: str, state_text: str, *, seq: int, reason: str = "") -> StateRef:
        """保存一条任务状态；同一 task 同一内容重复保存不新增记录。"""
        ...

    def load_latest(self, task_id: str) -> StateRecord | None:
        """读回该 task 的最近一条状态；没有则返回 None。"""
        ...

    def list_task_ids(self) -> list[str]:
        ...

    def health(self) -> dict[str, object]:
        ...
