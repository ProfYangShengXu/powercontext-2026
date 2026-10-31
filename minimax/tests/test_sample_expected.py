"""样例判据：上下文包必须包含 expected_sources，且每条都能回查数据库。

需要先 `make import`；没有索引时跳过（真正的证据在 docs/context-s1.log）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from mmctx.core.assembly import build_pack, query_from_task
from mmctx.core.selection import ensure_required, fuse
from mmctx.ports.store import SearchQuery
from mmctx.settings import build_settings


@pytest.fixture(scope="module")
def settings():
    return build_settings(Path(__file__).resolve().parent.parent)


def test_sample_expected_sources_are_in_pack(settings):
    from mmctx.adapters.seekdb_store import SeekdbStore

    if not Path(settings.seekdb_path).is_dir():
        pytest.skip("seekdb 数据目录不存在，先跑 make import")
    store = SeekdbStore(Path(settings.seekdb_path), embedding_dim=settings.embedding_dim).connect()
    try:
        if store.stats().current_chunks == 0:
            pytest.skip("索引为空，先跑 make import")
        task = yaml.safe_load((settings.samples_dir / "s1-order-filter" / "task.yaml").read_text(encoding="utf-8"))
        store.warm_vectors()
        arms = store.search(SearchQuery(query_from_task(task), limit=40))
        required = [
            (str(item.get("path", "")), str(item.get("symbol", "") or ""))
            for item in task.get("expected_sources") or []
            if item.get("path")
        ]
        hits = ensure_required(fuse(arms), arms, required)
        pack = build_pack(
            task, hits, spec_digest="test", budget_tokens=settings.budget_tokens, db={}, corpus_stats={}
        )
        paths = {source["path"] for source in pack["sources"]}
        missing = [item["path"] for item in task["expected_sources"] if item["path"] not in paths]
        assert not missing, f"missing expected sources: {missing}; got {sorted(paths)}"
        for source in pack["sources"]:
            assert store.lookup(str(source["chunk_uid"])) is not None
    finally:
        store.close()
