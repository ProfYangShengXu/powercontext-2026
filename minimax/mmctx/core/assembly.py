"""上下文包组装：把命中变成「模型能照着改代码」的一份材料。"""

from __future__ import annotations

from datetime import datetime, timezone

from mmctx.core.selection import estimate_tokens
from mmctx.ports.store import Hit

KIND_ORDER = ("spec", "code", "test")


def query_from_task(task: dict[str, object]) -> str:
    """任务描述 → 一条检索查询。标题、现象、关键词拼接，保留原文用词。"""
    parts = [str(task.get("title", "")), str(task.get("symptoms", "")), " ".join(task.get("keywords", []) or [])]
    return " ".join(part for part in parts if part).strip()


def build_pack(
    task: dict[str, object],
    hits: list[Hit],
    *,
    spec_digest: str,
    budget_tokens: int,
    db: dict[str, object],
    corpus_stats: dict[str, int],
) -> dict[str, object]:
    """按 token 预算裁剪命中；每条来源保留可反查的 path:line:hash 与正文。"""
    sources: list[dict[str, object]] = []
    used = 0
    budget_exceeded = False
    for hit in hits:
        cost = estimate_tokens(hit.content) + 8
        if used + cost > budget_tokens:
            # 预算不是硬截断的唯一目的：至少保留第一条来源，否则包不可用；
            # 但超预算这件事必须写进包，不能静默。
            budget_exceeded = True
            if sources:
                continue
        used += cost
        sources.append(
            {
                "chunk_uid": hit.chunk_uid,
                "doc_kind": hit.kind,
                "path": hit.path,
                "start_line": hit.start_line,
                "end_line": hit.end_line,
                "content_hash": hit.content_hash,
                "content": hit.content,
                "fts_score": hit.fts_score,
                "vec_dist": hit.vec_dist,
                "matched_arm": hit.arm,
            }
        )

    counts = {kind: sum(1 for item in sources if item["doc_kind"] == kind) for kind in KIND_ORDER}
    return {
        "task_id": str(task["task_id"]),
        "title": str(task.get("title", "")),
        "query": query_from_task(task),
        "spec_digest": spec_digest,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "budget_tokens": budget_tokens,
        "used_tokens": used,
        "sources": sources,
        "counts": counts,
        "db": db,
        "corpus": corpus_stats,
        "budget_exceeded": budget_exceeded,
    }


def render_markdown(pack: dict[str, object]) -> str:
    """把上下文包渲染成给人/给模型看的 md。来源顺序与 JSON 一致。"""
    lines = [
        f"# 上下文包 · {pack['task_id']}",
        "",
        f"- 任务：{pack['title']}",
        f"- 检索：{pack['query']}",
        f"- spec_digest：`{pack['spec_digest']}`",
        f"- 预算：{pack['budget_tokens']} tokens（估算使用 {pack['used_tokens']}）",
        f"- 来源数：{len(pack['sources'])}（spec {pack['counts']['spec']} / code {pack['counts']['code']} / test {pack['counts']['test']}）",
        "",
    ]
    fence = {"spec": "markdown", "code": "python", "test": "python"}
    for index, source in enumerate(pack["sources"], start=1):
        header = (
            f"## {index}. [{source['doc_kind']}] {source['path']}:"
            f"{source['start_line']}-{source['end_line']} (hash {source['content_hash']})"
        )
        lines.extend([header, "", f"```{fence.get(str(source['doc_kind']), '')}", str(source["content"]), "```", ""])
    return "\n".join(lines).rstrip() + "\n"


def check_expected_sources(pack: dict[str, object], task: dict[str, object]) -> list[str]:
    """样例判据：task.yaml 里声明的必要来源必须真的进了包。

    path 必须出现；声明了 symbol 时，该 path 至少有一个被引用的块正文包含该符号。
    这是项③的机器判据，不靠 tests 里的可选 skip。
    """
    by_path: dict[str, list[dict[str, object]]] = {}
    for source in pack.get("sources") or []:
        by_path.setdefault(str(source.get("path", "")), []).append(source)
    missing: list[str] = []
    for expected in task.get("expected_sources") or []:
        path = str(expected.get("path", ""))
        symbol = str(expected.get("symbol", "") or "")
        if path not in by_path:
            missing.append(f"{path}: path not in context pack")
            continue
        if symbol and not any(symbol in str(s.get("content", "")) for s in by_path[path]):
            missing.append(f"{path}#{symbol}: symbol not present in cited chunks")
    return missing
