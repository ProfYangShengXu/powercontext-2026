from __future__ import annotations

from conftest import SNAPSHOT
from coursecite.adapters.snapshot_store import SnapshotStore
from coursecite.core.service import SearchService


def test_search_service_records_every_search():
    store = SnapshotStore(SNAPSHOT)
    service = SearchService(store)
    for _ in range(3):
        response = service.search("上次讲 HNSW 是在第几讲")
        assert response.hits
    records = service.history(10)
    assert len(records) == 3
    assert all(record.store_mode == "snapshot" for record in records)
    assert all(record.top_ids for record in records)


def test_empty_query_is_rejected():
    service = SearchService(SnapshotStore(SNAPSHOT))
    try:
        service.search("   ")
    except ValueError as exc:
        assert "不能为空" in str(exc)
    else:
        raise AssertionError("空查询应当报错")
