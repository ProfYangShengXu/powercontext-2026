"""StorePort：存工程资料并检索（本项目由 seekdb 实现）。

端口只描述「要什么」：一段文本、要几条候选；不出现 SQL、表名、向量维度或
相似度参数——那些是实现细节，住在适配器里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

KINDS = ("spec", "code", "test")


@dataclass(frozen=True)
class Chunk:
    """一份文件切出来的一个块。行号 1-based、闭区间。"""

    doc_id: str
    ordinal: int
    kind: str
    path: str
    start_line: int
    end_line: int
    content: str
    content_hash: str
    symbol: str = ""

    @property
    def chunk_uid(self) -> str:
        return self.uid_for(self.doc_id, self.ordinal, self.content_hash)

    @staticmethod
    def uid_for(doc_id: str, ordinal: int, content_hash: str) -> str:
        """块身份 = (文件, 序号, 内容 hash) 的摘要。

        把内容 hash 放进身份，同一位置改内容就是一个新块：旧块置
        is_current=0 成为历史版本，而不会与主键冲突。
        """
        import hashlib

        raw = f"{doc_id}:{ordinal}:{content_hash}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:32]


@dataclass(frozen=True)
class Doc:
    """一份文件：身份由相对路径决定，版本由内容 hash 决定。"""

    doc_id: str
    path: str
    kind: str
    content: str
    content_hash: str
    chunks: Sequence[Chunk] = field(default_factory=tuple)


@dataclass(frozen=True)
class SearchQuery:
    """检索请求：自然语言查询 + 每路候选数。"""

    text: str
    limit: int = 12


@dataclass(frozen=True)
class Hit:
    """一条召回。fts_score / vec_dist 只用于展示与融合，可为 None。"""

    chunk_uid: str
    doc_id: str
    kind: str
    path: str
    start_line: int
    end_line: int
    content_hash: str
    content: str = ""
    fts_score: float | None = None
    vec_dist: float | None = None
    arm: str = ""


@dataclass(frozen=True)
class StoreStats:
    """用于运行说明与证据的最小统计。"""

    chunks: int
    docs: int
    current_chunks: int
    table: str
    parser: str


class StorePort(Protocol):
    """工程资料的写入与检索。"""

    def upsert_document(self, doc: Doc) -> dict[str, int]:
        """写入一份文件的全部块；返回 {"inserted": n, "invalidated": m}。"""
        ...

    def search(self, query: SearchQuery) -> list[Hit]:
        """单条语句同时取全文与向量候选，返回两路合并后的命中。"""
        ...

    def lookup(self, chunk_uid: str) -> Hit | None:
        """按块身份回查数据库；供 verify 证明「命中真的来自库」。"""
        ...

    def stats(self) -> StoreStats:
        ...

    def explain(self, query: SearchQuery) -> list[str]:
        """返回候选语句的 EXPLAIN 文本行，用于证明索引被使用。"""
        ...
