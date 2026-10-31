"""快照导出：把 LiveStore 的真实检索结果落成前端只渲染、不重排的 JSON。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .adapters.snapshot_store import SCHEMA
from .bootstrap.main import App
from .config import ConfigError
from .core.policy import DEFAULT_TOP_K


def load_queries(path: str | Path) -> list[dict]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"查询清单必须是非空数组：{path}")
    for item in raw:
        if not item.get("query"):
            raise ConfigError(f"查询项缺少 query 字段：{item!r}")
    return raw


def export_snapshot(app: App, queries_path: str | Path, out_path: str | Path, expected_dir: str | Path | None = None) -> dict:
    if app.settings.store != "live":
        raise ConfigError("快照必须从 LiveStore 导出：先 export COURSECITE_STORE=live 并连上 seekdb")
    queries = load_queries(queries_path)
    stats = app.search.stats()
    entries = []
    for item in queries:
        resp = app.search.search(
            item["query"],
            course=item.get("course"),
            max_lecture=item.get("max_lecture"),
        )
        entry = {
            "id": item.get("id", ""),
            "query": resp.query,
            "filters": resp.filters.to_dict(),
            "limit": DEFAULT_TOP_K,
            "elapsed_ms": resp.elapsed_ms,
            "channel": "hybrid",
            "results": [hit.to_dict(include_text=True) for hit in resp.hits],
        }
        entries.append(entry)
        if expected_dir is not None:
            expected = {
                "id": item.get("id", ""),
                "query": resp.query,
                "filters": resp.filters.to_dict(),
                "expect": {
                    "top_chunk_ids": [hit.chunk_id for hit in resp.hits],
                    "top_citations": [
                        {
                            "course": hit.citation.course,
                            "lecture": hit.citation.lecture,
                            "section": hit.citation.section,
                            "page": hit.citation.page,
                        }
                        for hit in resp.hits
                    ],
                },
                "must_match": item.get("must_match", {}),
            }
            target = Path(expected_dir) / f"{item.get('id', 'q')}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")

    snapshot = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "index": {
            "source": "seekdb LiveStore 真实检索",
            "store_mode": stats.get("mode"),
            "collection": stats.get("collection"),
            "chunks": stats.get("chunks"),
            "top_k": DEFAULT_TOP_K,
            "embedding": "all-MiniLM-L6-v2 / 384d",
            "retrieval": "seekdb HYBRID_SEARCH：全文 + 向量 + 过滤，库内 RRF",
        },
        "queries": entries,
    }
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot
