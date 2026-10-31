"""seekdb 集成测试：中文全文召回、单条 SQL、双索引、重复导入与规格换代。"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("pylibseekdb")

from mmctx.adapters.seekdb_store import SeekdbStore  # noqa: E402
from mmctx.core.chunking import chunk_text  # noqa: E402
from mmctx.core.hashing import content_hash, doc_id_for  # noqa: E402
from mmctx.ports.store import Doc, SearchQuery  # noqa: E402


def _tmp_root() -> str:
    if Path("/mnt/wsl").is_dir() and os.access("/mnt/wsl", os.W_OK):
        return "/mnt/wsl"
    return tempfile.gettempdir()


@pytest.fixture()
def store():
    directory = tempfile.mkdtemp(prefix="mmctx-test-", dir=_tmp_root())
    instance = SeekdbStore(Path(directory), embedding_dim=64).connect()
    try:
        yield instance
    finally:
        instance.close()
        shutil.rmtree(directory, ignore_errors=True)


def _doc(rel: str, text: str, kind: str = "spec") -> Doc:
    doc_id = doc_id_for(rel)
    return Doc(
        doc_id=doc_id,
        path=rel,
        kind=kind,
        content=text,
        content_hash=content_hash(text),
        chunks=chunk_text(kind, rel, text, doc_id),
    )


V1 = "# S-02\n\n## 订单筛选\n按状态筛选订单，金额下限使用应付金额。\n\n## 会员等级\n会员等级影响折扣。\n"
V2 = "# S-02\n\n## 订单筛选\n按状态与会员等级筛选订单，金额下限使用折扣后的应付金额且为大于等于。\n\n## 会员等级\n会员等级影响折扣。\n"


def test_chinese_fulltext_recall(store):
    store.upsert_document(_doc("corpus/specs/S-02.md", V1))
    hits = store.search(SearchQuery("订单筛选", limit=5))
    assert hits
    assert any("订单筛选" in hit.content for hit in hits)


def test_search_is_one_statement(store):
    store.upsert_document(_doc("corpus/specs/S-02.md", V1))
    store.search(SearchQuery("订单筛选", limit=5))
    assert store.statements_in_last_search == 1


def test_explain_uses_both_native_indexes(store):
    store.upsert_document(_doc("corpus/specs/S-02.md", V1))
    store.warm_vectors()
    lines = store.explain(SearchQuery("订单筛选", limit=5))
    text = "\n".join(lines)
    assert "TEXT RETRIEVAL SCAN" in text
    assert "VECTOR INDEX" in text


def test_reimport_same_content_is_idempotent(store):
    store.upsert_document(_doc("corpus/specs/S-02.md", V1))
    first = store.stats()
    store.upsert_document(_doc("corpus/specs/S-02.md", V1))
    second = store.stats()
    assert first.chunks == second.chunks
    assert second.current_chunks == first.current_chunks


def test_spec_update_invalidates_older_chunks(store):
    store.upsert_document(_doc("corpus/specs/S-02.md", V1))
    old = store.search(SearchQuery("订单筛选", limit=10))
    old_uid = next(hit.chunk_uid for hit in old if "按状态筛选订单" in hit.content)

    store.upsert_document(_doc("corpus/specs/S-02.md", V2))
    assert store.lookup(old_uid) is None  # 旧版本已失效
    new = store.search(SearchQuery("折扣后的应付金额", limit=10))
    assert any("大于等于" in hit.content for hit in new)
    assert all("按状态筛选订单，金额下限" not in hit.content for hit in new)
