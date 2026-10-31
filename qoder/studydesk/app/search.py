from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response

from .config import LOG_DIR, SEARCH_BRANCH_RESULTS, SEARCH_TOP_K
from .db import get_collection

router = APIRouter()
_log_lock = threading.Lock()

_LEADING_PHRASES = ("请问一下", "请问", "帮我找找", "帮我找", "帮我查", "帮我", "我想知道", "我想问")
_TRAILING_PHRASES = (
    "是怎么办",
    "是为什么呢",
    "是为什么",
    "是什么原因",
    "是什么",
    "是啥",
    "怎么办呢",
    "怎么办",
    "怎么理解",
    "怎么样",
    "怎么用",
    "怎么",
    "如何理解",
    "如何",
    "有哪些",
    "哪些",
    "为什么",
    "为何",
    "是什么",
    "什么",
)
_END_PUNCTUATION = "？?。！!，,；;：: \t\n"


def _fulltext_term(query: str) -> str:
    original = query.strip()
    term = original
    changed = True
    while changed:
        changed = False
        for prefix in _LEADING_PHRASES:
            if term.startswith(prefix) and len(term) > len(prefix):
                term = term[len(prefix) :].strip()
                changed = True
                break
        if term.startswith("什么是") and len(term) > 3:
            term = term[3:].strip()
            changed = True
    while term and term[-1] in _END_PUNCTUATION:
        term = term[:-1].strip()
    for suffix in _TRAILING_PHRASES:
        if term.endswith(suffix) and len(term) > len(suffix):
            term = term[: -len(suffix)].strip()
            break
    while term and term[-1] in _END_PUNCTUATION:
        term = term[:-1].strip()
    return term or original


def _append_log(entry: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"search_{datetime.now().astimezone():%Y%m%d}.jsonl"
    line = json.dumps(entry, ensure_ascii=False, default=str)
    with _log_lock:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _first_row(value: Any) -> list[Any]:
    if not value:
        return []
    if isinstance(value, list) and value and isinstance(value[0], list):
        return value[0]
    return value if isinstance(value, list) else []


def _location(metadata: dict[str, Any]) -> str:
    parts = []
    page = metadata.get("page")
    if page is not None:
        parts.append(f"第 {page} 页")
    chunk_index = metadata.get("chunk_index")
    if chunk_index is not None:
        parts.append(f"第 {int(chunk_index) + 1} 块")
    return " · ".join(parts) if parts else "未标注位置"


def perform_search(query: str, course: str | None) -> list[dict[str, Any]]:
    term = _fulltext_term(query)
    query_config: dict[str, Any] = {
        "where_document": {"$contains": term},
        "n_results": SEARCH_BRANCH_RESULTS,
    }
    knn_config: dict[str, Any] = {
        "query_texts": [query],
        "n_results": SEARCH_BRANCH_RESULTS,
    }
    if course:
        where = {"course": {"$eq": course}}
        query_config["where"] = where
        knn_config["where"] = where

    started = time.perf_counter()
    log_base = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "request": {
            "q": query,
            "course": course,
            "fulltext_term": term,
            "hybrid_query": query_config,
            "hybrid_knn": knn_config,
            "rank": {"rrf": {}},
            "final_n_results": SEARCH_TOP_K,
        },
    }

    try:
        collection = get_collection()
        raw = collection.hybrid_search(
            query=query_config,
            knn=knn_config,
            rank={"rrf": {}},
            n_results=SEARCH_TOP_K,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        _append_log(
            {
                **log_base,
                "response": None,
                "elapsed_ms": elapsed_ms,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        raise HTTPException(status_code=500, detail=f"检索失败：{exc}") from exc

    ids = _first_row(raw.get("ids"))
    documents = _first_row(raw.get("documents"))
    metadatas = _first_row(raw.get("metadatas"))
    distances = _first_row(raw.get("distances"))

    results: list[dict[str, Any]] = []
    for position, doc_id in enumerate(ids):
        document = documents[position] if position < len(documents) else ""
        metadata = metadatas[position] if position < len(metadatas) else {}
        metadata = metadata or {}
        distance = distances[position] if position < len(distances) else None
        matched_by = (
            "fulltext+vector"
            if term and term.casefold() in (document or "").casefold()
            else "vector"
        )
        results.append(
            {
                "doc_id": doc_id,
                "text": document,
                "source_file": metadata.get("source_file", ""),
                "location": _location(metadata),
                "distance": round(float(distance), 6) if distance is not None else None,
                "matched_by": matched_by,
            }
        )

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    _append_log(
        {
            **log_base,
            "response": {
                "ids": ids,
                "documents": documents,
                "metadatas": metadatas,
                "distances": distances,
            },
            "results": results,
            "result_count": len(results),
            "elapsed_ms": elapsed_ms,
            "ok": True,
            "error": None,
        }
    )
    return results


@router.get("/search")
def search(
    response: Response,
    q: str = Query(min_length=1),
    course: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="q 不能为空")
    normalized_course = course.strip() if course and course.strip() else None
    response.headers["X-Search-Term"] = quote(_fulltext_term(query), safe="")
    return perform_search(query, normalized_course)
