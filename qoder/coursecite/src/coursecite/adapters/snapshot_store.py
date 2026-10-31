"""读预生成 JSON 的只读实现（线上静态站 / 无数据库演示用）。

红线：本文件里**不存在**任何排序、融合、加权、截断逻辑。
快照里的 `results` 就是最终顺序，本适配器原样返回。判据见 README「分层验收」。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from ..core.models import Chunk, Filters, Hit, Query, Ref, RetrievalRecord
from ..ports.store import NoMatch, ReadOnlyStore, StoreError

SCHEMA = "coursecite.snapshot.v1"


class SnapshotStore:
    mode = "snapshot"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise StoreError(
                f"快照文件不存在：{self.path}\n"
                f"生成方式：COURSECITE_STORE=live coursecite snapshot export"
            )
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StoreError(f"快照 JSON 解析失败：{self.path}: {exc}") from exc
        if raw.get("schema") != SCHEMA:
            raise StoreError(f"快照 schema 不匹配：期望 {SCHEMA}，实际 {raw.get('schema')!r}")
        self._raw = raw
        self._generated_at = str(raw.get("generated_at", ""))
        self._index = dict(raw.get("index", {}))
        self._entries: dict[tuple[str, tuple], list[Hit]] = {}
        self._queries: list[dict[str, Any]] = list(raw.get("queries", []))
        self._records: list[RetrievalRecord] = []
        self._chunks: dict[str, Chunk] = {}

        for entry in self._queries:
            query = Query(
                text=str(entry["query"]),
                filters=Filters.from_dict(entry.get("filters")),
            )
            hits = [Hit.from_dict(item) for item in entry.get("results", [])]
            self._entries[(query.normalized, query.filters.key())] = hits
            for hit in hits:
                if hit.text is not None:
                    self._chunks.setdefault(
                        hit.chunk_id,
                        Chunk(
                            id=hit.chunk_id,
                            text=hit.text,
                            snippet=hit.snippet,
                            citation=hit.citation,
                            content_hash="",
                        ),
                    )

    @property
    def available_queries(self) -> list[str]:
        return [str(entry["query"]) for entry in self._queries]

    def search(self, query: Query) -> list[Hit]:
        hits = self._entries.get((query.normalized, query.filters.key()))
        if hits is None:
            available = "；".join(self.available_queries)
            raise NoMatch(
                f"离线快照中没有这个问题：{query.text!r}\n"
                f"快照只回放已收录的查询：{available}\n"
                f"真实自由检索请用 LiveStore（COURSECITE_STORE=live）。"
            )
        # 只取已排好序列表的前缀（渲染层截断），不排序、不重算分数。
        return list(hits[: max(1, query.limit)])

    def obtain(self, ref: Ref) -> Chunk | None:
        return self._chunks.get(ref.chunk_id)

    def put(self, chunks: Sequence[Chunk]) -> int:
        raise ReadOnlyStore("快照是只读产物；重建请用 LiveStore 运行 `coursecite snapshot export`")

    def reset(self) -> None:
        raise ReadOnlyStore("快照是只读产物，不能 reset")

    def record_search(self, record: RetrievalRecord) -> None:
        # 静态站没有数据库：留痕只保存在当前进程内，用于页面下方展示。
        self._records.insert(0, record)

    def history(self, limit: int) -> list[RetrievalRecord]:
        return list(self._records[:limit])

    def stats(self) -> dict:
        return {
            "mode": self.mode,
            "snapshot": str(self.path),
            "generated_at": self._generated_at,
            "chunks": int(self._index.get("chunks", 0)),
            "queries": len(self._queries),
            "top_k": int(self._index.get("top_k", 0)),
            "source": self._index.get("source", ""),
        }
