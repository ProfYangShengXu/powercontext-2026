"""真连 seekdb 的集成测试（默认跳过，不拖慢无库环境）。

跑法（先 `docker compose up -d`，再 `coursecite ingest`）：

    COURSECITE_LIVE=1 SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2881 \
        pytest tests/test_live_integration.py -v

它补上 snapshot-only 测试缺失的一环：证明 LiveStore 真的返回带出处的 hit，
每次检索真的往 retrieval_log 写一行，并且从真库重新导出的快照与仓库里的冻结
工件逐条一致（而不是只在自己生成的工件内部自洽）。
"""
from __future__ import annotations

import json
import os

import pytest
from conftest import QUERIES, SNAPSHOT

from coursecite.bootstrap.main import build_app
from coursecite.config import Settings
from coursecite.export import export_snapshot
from coursecite.ports.store import StoreUnavailable

pytestmark = pytest.mark.skipif(
    os.environ.get("COURSECITE_LIVE") != "1",
    reason="需要真 seekdb：设 COURSECITE_LIVE=1 并先 docker compose up -d",
)


def _settings() -> Settings:
    env = {**os.environ, "COURSECITE_STORE": "live"}
    env.setdefault("SEEKDB_HOST", "127.0.0.1")
    env.setdefault("SEEKDB_PORT", "2881")
    return Settings.from_env(env)


@pytest.fixture(scope="module")
def live_app():
    try:
        return build_app(_settings())
    except StoreUnavailable as exc:  # 明确失败，不要悄悄 skip 成绿
        pytest.fail(f"COURSECITE_LIVE=1 但 seekdb 不可用：{exc}")


def test_live_search_cites_source_and_writes_log(live_app):
    before = int(live_app.store.stats()["retrieval_log_rows"])
    response = live_app.search.search("上次讲 HNSW 是在第几讲", limit=3)

    assert response.store_mode == "live"
    assert response.hits, "live 检索应有命中"
    assert [hit.rank for hit in response.hits] == list(range(1, len(response.hits) + 1))

    citation = response.hits[0].citation
    assert citation.course and citation.lecture > 0
    assert citation.section and citation.section_title
    assert citation.path
    assert live_app.store.stats()["chunks"] >= len(response.hits)

    after = int(live_app.store.stats()["retrieval_log_rows"])
    assert after == before + 1, "每次成功检索必须写且只写一行 retrieval_log"


def test_live_export_reproduces_committed_snapshot(live_app, tmp_path):
    fresh = export_snapshot(
        live_app,
        QUERIES,
        tmp_path / "snapshots.json",
        expected_dir=tmp_path / "expected",
    )
    frozen = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    assert fresh["index"] == frozen["index"]
    assert len(fresh["queries"]) == len(frozen["queries"])
    for produced, committed in zip(fresh["queries"], frozen["queries"]):
        assert produced["query"] == committed["query"]
        assert [r["chunk_id"] for r in produced["results"]] == [
            r["chunk_id"] for r in committed["results"]
        ]
        assert [r["score"] for r in produced["results"]] == [
            r["score"] for r in committed["results"]
        ]
        assert [r["citation"] for r in produced["results"]] == [
            r["citation"] for r in committed["results"]
        ]
