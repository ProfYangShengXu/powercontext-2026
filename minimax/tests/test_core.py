"""核心规则单元测试：切块、融合、补丁守卫、状态形状、来源反查。"""

from __future__ import annotations

from mmctx.core.chunking import chunk_text
from mmctx.core.hashing import content_hash, corpus_digest
from mmctx.core.patch import extract_diff, guard_patch
from mmctx.core.selection import fuse
from mmctx.core.state_model import build_state, is_stale, pack_state, state_hash
from mmctx.core.verify import verify_sources
from mmctx.ports.store import Chunk, Hit


def test_spec_chunks_follow_headings_and_are_reconstructible():
    text = "# t\n\n## A\nx\n\n## B\ny\n"
    chunks = chunk_text("spec", "a.md", text, "d1")
    assert [(c.start_line, c.end_line) for c in chunks] == [(1, 1), (3, 4), (6, 7)]
    assert [c.content for c in chunks] == ["# t", "## A\nx", "## B\ny"]


def test_code_chunks_follow_top_level_defs():
    text = "import os\n\n\ndef a():\n    return 1\n\n\ndef b():\n    return 2\n"
    chunks = chunk_text("code", "demo/a.py", text, "d1")
    assert [c.symbol for c in chunks] == ["", "a", "b"]
    assert chunks[1].content.startswith("def a():")


def test_chunk_uid_is_content_addressed():
    text = "# t\n\n## A\nx\n"
    first = chunk_text("spec", "a.md", text, "d1")[1]
    again = chunk_text("spec", "a.md", text, "d1")[1]
    changed = chunk_text("spec", "a.md", "# t\n\n## A\nxx\n", "d1")[1]
    assert first.chunk_uid == again.chunk_uid
    assert first.chunk_uid != changed.chunk_uid


def test_corpus_digest_changes_with_content_not_order():
    items = [("b.md", "B"), ("a.md", "A")]
    assert corpus_digest(items) == corpus_digest(list(reversed(items)))
    assert corpus_digest(items) != corpus_digest([("a.md", "A"), ("b.md", "B2")])


def _hit(uid: str, kind: str, arm: str, path: str = "p") -> Hit:
    return Hit(
        chunk_uid=uid,
        doc_id="d",
        kind=kind,
        path=path,
        start_line=1,
        end_line=2,
        content_hash="h",
        content="x",
        arm=arm,
    )


def test_fuse_quota_keeps_specs_even_if_code_ranks_higher():
    arms = [
        _hit("c1", "code", "fts"),
        _hit("c2", "code", "fts"),
        _hit("c3", "code", "fts"),
        _hit("s1", "spec", "vector"),
        _hit("t1", "test", "vector"),
    ]
    fused = fuse(arms, quota={"spec": 1, "code": 1, "test": 1}, total=3)
    kinds = {hit.kind for hit in fused}
    assert kinds == {"spec", "code", "test"}
    assert fused[0].kind == "spec"


def test_fuse_merges_same_chunk_from_two_arms():
    arms = [_hit("x", "spec", "fts"), _hit("x", "spec", "vector")]
    fused = fuse(arms)
    assert len(fused) == 1
    assert fused[0].arm == "fts+vector"


def test_extract_diff_strips_think_and_fence():
    reply = (
        "<think>thinking about it</think>\n"
        "```diff\n--- a/demo_project/shop/filters.py\n+++ b/demo_project/shop/filters.py\n"
        "@@ -1 +1 @@\n-a\n+b\n```\n"
    )
    diff = extract_diff(reply)
    assert diff.startswith("--- a/demo_project/shop/filters.py")
    assert "think" not in diff


def test_guard_patch_rejects_test_files_and_outside_paths():
    good = "--- a/demo_project/shop/filters.py\n+++ b/demo_project/shop/filters.py\n@@ -1 +1 @@\n-a\n+b\n"
    paths, errors = guard_patch(good)
    assert paths == ["demo_project/shop/filters.py"] and errors == []

    bad_test = "--- a/demo_project/tests/test_filters.py\n+++ b/demo_project/tests/test_filters.py\n@@ -1 +1 @@\n-a\n+b\n"
    assert guard_patch(bad_test)[1]

    outside = "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-a\n+b\n"
    assert guard_patch(outside)[1]


def test_state_pack_and_staleness():
    state = build_state(
        task_id="t1",
        title="x",
        run_id="r1",
        spec_digest="d1",
        context_sha256="c1",
        applied_commit="abc",
        status="applied",
        todo=["a"],
        seq=1,
        updated_at="now",
    )
    text = pack_state(state)
    assert '"task_id":"t1"' in text
    assert not is_stale(state, "d1")
    assert is_stale(state, "d2")
    # 指纹忽略 seq / updated_at：重复 state 才可能被判成「未变化」。
    moved = dict(state, seq=9, updated_at="later")
    assert state_hash(state) == state_hash(moved)


def test_verify_sources_detects_tampering():
    text = "# t\n\n## A\nhello\n"
    chunk = chunk_text("spec", "a.md", text, "d1")[1]
    pack = {
        "sources": [
            {
                "chunk_uid": chunk.chunk_uid,
                "path": "a.md",
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "content_hash": chunk.content_hash,
                "content": chunk.content,
            }
        ]
    }
    assert verify_sources(pack, lambda path: text) == []
    tampered = {"sources": [dict(pack["sources"][0], content_hash="deadbeef")]}
    assert verify_sources(tampered, lambda path: text)
    missing = {"sources": [dict(pack["sources"][0], path="nope.md")]}
    assert verify_sources(missing, lambda path: (_ for _ in ()).throw(FileNotFoundError(path)))


def test_check_expected_sources_reports_missing_path_and_symbol():
    from mmctx.core.assembly import check_expected_sources

    pack = {
        "sources": [
            {"path": "corpus/specs/S-02.md", "content": "## 筛选\n内容"},
            {"path": "demo_project/shop/filters.py", "content": "def filter_orders(...):"},
        ]
    }
    task = {
        "expected_sources": [
            {"path": "corpus/specs/S-02.md"},
            {"path": "demo_project/shop/filters.py", "symbol": "filter_orders"},
            {"path": "demo_project/tests/test_filters.py"},
            {"path": "demo_project/shop/filters.py", "symbol": "not_here"},
        ]
    }
    missing = check_expected_sources(pack, task)
    assert any("test_filters.py" in item for item in missing)
    assert any("not_here" in item for item in missing)
    assert len(missing) == 2


def test_pack_budget_keeps_first_source_but_marks_overflow():
    from mmctx.core.assembly import build_pack

    big = Hit(
        chunk_uid="u1",
        doc_id="d",
        kind="spec",
        path="p.md",
        start_line=1,
        end_line=2,
        content_hash="h",
        content="中" * 5000,
    )
    pack = build_pack({"task_id": "t"}, [big], spec_digest="d", budget_tokens=100, db={}, corpus_stats={})
    assert len(pack["sources"]) == 1
    assert pack["budget_exceeded"] is True
    assert pack["used_tokens"] > pack["budget_tokens"]
