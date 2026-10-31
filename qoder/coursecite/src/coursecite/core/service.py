"""应用服务：CLI 与 Web 共用同一套检索入口。

排序/融合不在这里做——它属于存储侧；这里只做查询构造、留痕、结果包装。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from ..ports.store import StorePort
from .ingest import IngestReport, load_corpus
from .models import Filters, Hit, Query, Ref, RetrievalRecord, SearchResponse
from .policy import DEFAULT_TOP_K
from .retrieval import make_query


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SearchService:
    def __init__(self, store: StorePort) -> None:
        self._store = store

    @property
    def store_mode(self) -> str:
        return self._store.mode

    def search(
        self,
        text: str,
        *,
        course: str | None = None,
        max_lecture: int | None = None,
        limit: int = DEFAULT_TOP_K,
    ) -> SearchResponse:
        if not text or not text.strip():
            raise ValueError("查询不能为空")
        query = make_query(text, course=course, max_lecture=max_lecture, limit=limit)
        started = time.perf_counter()
        hits = self._store.search(query)
        elapsed_ms = max(0, int(round((time.perf_counter() - started) * 1000)))
        record = RetrievalRecord(
            query=query.normalized,
            filters=query.filters,
            top_ids=tuple(hit.chunk_id for hit in hits),
            elapsed_ms=elapsed_ms,
            channel="hybrid",
            store_mode=self._store.mode,
            created_at=_now_iso(),
        )
        self._store.record_search(record)
        return SearchResponse(
            query=query.text,
            filters=query.filters,
            hits=hits,
            elapsed_ms=elapsed_ms,
            store_mode=self._store.mode,
        )

    def obtain(self, chunk_id: str):
        return self._store.obtain(Ref(chunk_id))

    def history(self, limit: int = 5) -> list[RetrievalRecord]:
        return self._store.history(limit)

    def stats(self) -> dict:
        return self._store.stats()

    def available_queries(self) -> list[str]:
        return list(getattr(self._store, "available_queries", []))


class IngestService:
    def __init__(self, store: StorePort) -> None:
        self._store = store

    def ingest(self, root: str, *, rebuild: bool = False) -> IngestReport:
        chunks, report = load_corpus(root)
        if rebuild:
            self._store.reset()
        report.written = self._store.put(chunks)
        return report
