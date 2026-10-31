from __future__ import annotations

from conftest import SAMPLES
from coursecite.core.ingest import load_corpus, split_sections


def test_corpus_shape_and_citations_complete():
    chunks, report = load_corpus(SAMPLES)
    assert report.files == 22
    assert report.chunks == 66
    assert len({chunk.id for chunk in chunks}) == 66
    for chunk in chunks:
        citation = chunk.citation
        assert citation.course in {"CSC3170", "CSC4180", "CSC5210"}
        assert citation.lecture > 0
        assert citation.section
        assert citation.section_title
        assert citation.page is not None and citation.page > 0
        assert citation.path.endswith(".md")
        assert chunk.snippet
        assert chunk.content_hash


def test_double_load_is_idempotent():
    first, _ = load_corpus(SAMPLES)
    second, _ = load_corpus(SAMPLES)
    assert [chunk.id for chunk in first] == [chunk.id for chunk in second]


def test_page_marker_updates_and_can_be_absent():
    text = "## 2.1 有页码\n<!-- page: 7 -->\n正文一\n## 2.2 无页码\n正文二"
    chunks = split_sections("x.md", text, course="CSC0000", course_name="测试", lecture=2)
    assert len(chunks) == 2
    assert chunks[0].citation.page == 7
    assert chunks[1].citation.page is None


def test_missing_heading_still_indexes_whole_file():
    chunks = split_sections("x.txt", "一段没有标题的笔记", course="CSC0000", course_name="测试", lecture=1)
    assert len(chunks) == 1
    assert chunks[0].citation.section == "全文"
