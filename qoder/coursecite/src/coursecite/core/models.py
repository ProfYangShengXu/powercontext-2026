"""领域模型。

这里不出现 seekdb / pyseekdb / fastapi / JSON 文件路径等实现细节：
端口两侧共享的只有这些类型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .policy import DEFAULT_TOP_K


@dataclass(frozen=True)
class Filters:
    """检索的结构化过滤条件。"""

    course: Optional[str] = None
    max_lecture: Optional[int] = None

    def is_empty(self) -> bool:
        return self.course is None and self.max_lecture is None

    def key(self) -> tuple[Optional[str], Optional[int]]:
        return (self.course, self.max_lecture)

    def to_dict(self) -> dict[str, Any]:
        return {"course": self.course, "max_lecture": self.max_lecture}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Filters":
        data = data or {}
        course = data.get("course")
        lecture = data.get("max_lecture")
        return cls(course=course, max_lecture=int(lecture) if lecture is not None else None)


@dataclass(frozen=True)
class Citation:
    """可核对到原文的出处。"""

    course: str
    course_name: str
    lecture: int
    section: str
    section_title: str
    page: Optional[int]
    path: str
    line_start: int = 0

    def label(self) -> str:
        page = f"p.{self.page}" if self.page is not None else "p.—"
        section = f"{self.section} {self.section_title}".strip()
        return f"{self.course} · 第 {self.lecture} 讲 · {section} · {page}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "course": self.course,
            "course_name": self.course_name,
            "lecture": self.lecture,
            "section": self.section,
            "section_title": self.section_title,
            "page": self.page,
            "path": self.path,
            "line_start": self.line_start,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Citation":
        return cls(
            course=str(data["course"]),
            course_name=str(data.get("course_name", data["course"])),
            lecture=int(data["lecture"]),
            section=str(data.get("section", "")),
            section_title=str(data.get("section_title", "")),
            page=None if data.get("page") is None else int(data["page"]),
            path=str(data.get("path", "")),
            line_start=int(data.get("line_start", 0)),
        )


@dataclass(frozen=True)
class Chunk:
    """入库前的段落。id 由内容决定，重复 ingest 幂等。"""

    id: str
    text: str
    snippet: str
    citation: Citation
    content_hash: str

    def embedding_text(self) -> str:
        c = self.citation
        head = f"{c.course} 第{c.lecture}讲 {c.section} {c.section_title}".strip()
        return f"{head}\n{self.text}".strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.id,
            "text": self.text,
            "snippet": self.snippet,
            "content_hash": self.content_hash,
            "citation": self.citation.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Chunk":
        return cls(
            id=str(data["chunk_id"]),
            text=str(data.get("text", "")),
            snippet=str(data.get("snippet", "")),
            citation=Citation.from_dict(data["citation"]),
            content_hash=str(data.get("content_hash", "")),
        )


@dataclass(frozen=True)
class Ref:
    """不透明句柄。端口只认它，不认识表名/rowid。"""

    chunk_id: str

    def to_dict(self) -> dict[str, str]:
        return {"chunk_id": self.chunk_id}


@dataclass(frozen=True)
class Query:
    text: str
    filters: Filters = field(default_factory=Filters)
    limit: int = DEFAULT_TOP_K

    @property
    def normalized(self) -> str:
        return " ".join(self.text.split())

    def key(self) -> tuple[str, tuple[Optional[str], Optional[int]], int]:
        return (self.normalized, self.filters.key(), self.limit)


@dataclass(frozen=True)
class Hit:
    """已排好序的单条结果。rank 由存储侧给出，渲染层不得重排。"""

    rank: int
    chunk_id: str
    score: float
    channels: tuple[str, ...]
    snippet: str
    citation: Citation
    text: Optional[str] = None

    def to_dict(self, include_text: bool = True) -> dict[str, Any]:
        data = {
            "rank": self.rank,
            "chunk_id": self.chunk_id,
            "score": round(float(self.score), 6),
            "channels": list(self.channels),
            "snippet": self.snippet,
            "citation": self.citation.to_dict(),
        }
        if include_text and self.text is not None:
            data["text"] = self.text
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any], include_text: bool = True) -> "Hit":
        return cls(
            rank=int(data["rank"]),
            chunk_id=str(data["chunk_id"]),
            score=float(data.get("score", 0.0)),
            channels=tuple(data.get("channels", [])),
            snippet=str(data.get("snippet", "")),
            citation=Citation.from_dict(data["citation"]),
            text=str(data["text"]) if (include_text and data.get("text") is not None) else None,
        )


@dataclass(frozen=True)
class RetrievalRecord:
    """一次检索的留痕。LiveStore 写入 seekdb 的 retrieval_log 表。"""

    query: str
    filters: Filters
    top_ids: tuple[str, ...]
    elapsed_ms: int
    channel: str
    store_mode: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "filters": self.filters.to_dict(),
            "top_ids": list(self.top_ids),
            "elapsed_ms": self.elapsed_ms,
            "channel": self.channel,
            "store_mode": self.store_mode,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class SearchResponse:
    query: str
    filters: Filters
    hits: list[Hit]
    elapsed_ms: int
    store_mode: str

    def to_dict(self, include_text: bool = True) -> dict[str, Any]:
        return {
            "query": self.query,
            "filters": self.filters.to_dict(),
            "elapsed_ms": self.elapsed_ms,
            "store_mode": self.store_mode,
            "hits": [h.to_dict(include_text=include_text) for h in self.hits],
        }
