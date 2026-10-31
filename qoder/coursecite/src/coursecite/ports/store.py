"""StorePort：core 侧定义的唯一存储端口。

强写法：不出现表名、索引名、连接串、JSON 路径。换 seekdb <-> 快照回放，
只有装配点改一行，core 与 ports 的 import 行逐字节不变。
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from ..core.models import Chunk, Hit, Query, Ref, RetrievalRecord


class StoreError(RuntimeError):
    """存储层错误的基类。禁止静默降级。"""


class StoreUnavailable(StoreError):
    """数据库连不上 / 初始化失败。"""


class ReadOnlyStore(StoreError):
    """当前实现不支持写入（例如只读快照）。"""


class NoMatch(StoreError):
    """没有该查询的可回放结果（快照模式下是显式失败，不是空结果）。"""


class IncompleteCorpus(StoreError):
    """语料为空或缺少必要字段。"""


@runtime_checkable
class StorePort(Protocol):
    mode: str

    def search(self, query: Query) -> list[Hit]:
        """返回已排好序的最终结果（rank 由 1 递增）。"""
        ...

    def obtain(self, ref: Ref) -> Chunk | None:
        """按不透明句柄取回原文段落。"""
        ...

    def put(self, chunks: Sequence[Chunk]) -> int:
        """幂等写入，返回写入条数。"""
        ...

    def reset(self) -> None:
        """清空索引（`--rebuild` 用）。"""
        ...

    def record_search(self, record: RetrievalRecord) -> None:
        """检索留痕。"""
        ...

    def history(self, limit: int) -> list[RetrievalRecord]:
        ...

    def stats(self) -> dict:
        ...
