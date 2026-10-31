from __future__ import annotations

from coursecite.core.models import Citation
from coursecite.core.retrieval import render_citation
from coursecite.core.models import Hit


def _citation(page=17):
    return Citation(
        course="CSC5210",
        course_name="信息检索",
        lecture=3,
        section="3.1",
        section_title="HNSW 的构造与查询",
        page=page,
        path="CSC5210/L03-近似最近邻.md",
        line_start=2,
    )


def test_citation_label_is_human_readable():
    assert render_citation(Hit(1, "id", 0.1, ("hybrid",), "片段", _citation())) == (
        "CSC5210 · 第 3 讲 · 3.1 HNSW 的构造与查询 · p.17"
    )


def test_citation_without_page_is_not_fabricated():
    assert render_citation(Hit(1, "id", 0.1, ("hybrid",), "片段", _citation(page=None))).endswith("p.—")


def test_citation_roundtrip():
    citation = _citation()
    assert Citation.from_dict(citation.to_dict()) == citation
