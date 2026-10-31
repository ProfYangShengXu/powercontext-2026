from __future__ import annotations

import json

from conftest import EXPECTED, QUERIES, SNAPSHOT
from coursecite.core.policy import DEFAULT_TOP_K


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_snapshot_matches_live_export_and_expectations():
    snapshot = _load(SNAPSHOT)
    entries = {entry["id"]: entry for entry in snapshot["queries"]}
    queries = _load(QUERIES)

    assert snapshot["index"]["top_k"] == DEFAULT_TOP_K
    assert snapshot["index"]["chunks"] == 66
    assert len(entries) == len(queries) == 5

    for item in queries:
        entry = entries[item["id"]]
        expected = _load(EXPECTED / f"{item['id']}.json")
        assert [row["chunk_id"] for row in entry["results"]] == expected["expect"]["top_chunk_ids"]
        assert entry["query"] == item["query"]

        must = item["must_match"]
        matched = [
            row
            for row in entry["results"]
            if row["citation"]["course"] == must["course"]
            and row["citation"]["lecture"] == must["lecture"]
            and row["citation"]["section"] == must["section"]
        ]
        assert matched, f"{item['id']} 的前 8 条里没有期望段落 {must}"
        # 演示要求：每条查询的第一条就是正确出处。
        assert entry["results"][0]["citation"]["course"] == must["course"]
        assert entry["results"][0]["citation"]["lecture"] == must["lecture"]
        assert entry["results"][0]["citation"]["section"] == must["section"]


def test_snapshot_results_carry_every_citation_field():
    snapshot = _load(SNAPSHOT)
    for entry in snapshot["queries"]:
        for row in entry["results"]:
            citation = row["citation"]
            assert citation["course"] and citation["course_name"]
            assert citation["lecture"] > 0
            assert citation["section"] and citation["section_title"]
            assert citation["page"] is not None
            assert citation["path"]
            assert row["text"] and row["snippet"]
