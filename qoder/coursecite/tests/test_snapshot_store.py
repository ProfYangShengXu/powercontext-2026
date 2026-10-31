from __future__ import annotations

import json

import pytest
from conftest import SNAPSHOT
from coursecite.adapters.snapshot_store import SnapshotStore
from coursecite.core.models import Filters, Hit, Query
from coursecite.ports.store import NoMatch, ReadOnlyStore


@pytest.fixture()
def payload() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


@pytest.fixture()
def store() -> SnapshotStore:
    return SnapshotStore(SNAPSHOT)


def test_snapshot_returns_pre_sorted_results_verbatim(store: SnapshotStore, payload: dict):
    entry = payload["queries"][0]
    query = Query(text=entry["query"], filters=Filters.from_dict(entry["filters"]), limit=entry["limit"])
    hits = store.search(query)
    assert [hit.chunk_id for hit in hits] == [item["chunk_id"] for item in entry["results"]]
    assert [hit.rank for hit in hits] == list(range(1, len(hits) + 1))
    assert [hit.score for hit in hits] == [item["score"] for item in entry["results"]]


def test_snapshot_does_not_reorder_even_if_scores_increase():
    """伪造一个分数递增的快照：适配器必须原样返回，证明它没有重排。"""
    payload = {
        "schema": "coursecite.snapshot.v1",
        "generated_at": "test",
        "index": {"chunks": 2, "top_k": 2},
        "queries": [
            {
                "query": "q",
                "filters": {},
                "limit": 2,
                "results": [
                    {"rank": 1, "chunk_id": "a", "score": 0.1, "channels": ["hybrid"],
                     "snippet": "a", "citation": {"course": "C", "lecture": 1, "section": "1", "section_title": "t", "page": 1, "path": "a.md"}},
                    {"rank": 2, "chunk_id": "b", "score": 0.9, "channels": ["hybrid"],
                     "snippet": "b", "citation": {"course": "C", "lecture": 1, "section": "2", "section_title": "t", "page": 2, "path": "a.md"}},
                ],
            }
        ],
    }
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "snap.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        fake = SnapshotStore(path)
        hits = fake.search(Query(text="q", limit=2))
    assert [hit.chunk_id for hit in hits] == ["a", "b"]
    assert hits[0].score == 0.1 and hits[1].score == 0.9


def test_unknown_query_is_explicit_failure(store: SnapshotStore):
    with pytest.raises(NoMatch) as excinfo:
        store.search(Query(text="快照里没有的问题"))
    assert "离线快照中没有这个问题" in str(excinfo.value)


def test_filter_mismatch_is_not_silently_ignored(store: SnapshotStore):
    with pytest.raises(NoMatch):
        store.search(Query(text="上次讲 HNSW 是在第几讲", filters=Filters(course="CSC3170")))


def test_snapshot_is_read_only(store: SnapshotStore):
    with pytest.raises(ReadOnlyStore):
        store.put([])
    with pytest.raises(ReadOnlyStore):
        store.reset()


def test_obtain_and_in_memory_log(store: SnapshotStore):
    entry = store._raw["queries"][0]
    first = entry["results"][0]
    chunk = store.obtain(__import__("coursecite.core.models", fromlist=["Ref"]).Ref(first["chunk_id"]))
    assert chunk is not None and chunk.text
    from coursecite.core.models import RetrievalRecord

    store.record_search(RetrievalRecord("q", Filters(), ("a",), 3, "hybrid", "snapshot", "now"))
    assert store.history(1)[0].query == "q"


def test_limit_slices_prefix_without_reordering(store: SnapshotStore):
    full = store.search(Query(text="RRF 的 k 取多少"))
    two = store.search(Query(text="RRF 的 k 取多少", limit=2))
    assert [hit.chunk_id for hit in two] == [hit.chunk_id for hit in full[:2]]
