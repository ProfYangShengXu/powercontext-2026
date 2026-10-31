"""检索结果的组装与渲染规则（唯一实现处）。

排序、融合在存储侧（seekdb 库内 / 快照预生成时）完成；
core 只负责：查询规范化、命中判空、出处渲染、格式化输出。
"""

from __future__ import annotations

from typing import Iterable, Sequence

from .models import Filters, Hit, Query, SearchResponse
from .policy import DEFAULT_TOP_K


class NoResultError(LookupError):
    """检索成功但没有任何命中。由调用方决定是否算失败。"""


def normalize_query(text: str) -> str:
    return " ".join(text.split())


def make_query(
    text: str,
    *,
    course: str | None = None,
    max_lecture: int | None = None,
    limit: int = DEFAULT_TOP_K,
) -> Query:
    if limit <= 0:
        raise ValueError("limit 必须为正数")
    return Query(text=text, filters=Filters(course=course, max_lecture=max_lecture), limit=limit)


def render_citation(hit: Hit) -> str:
    return hit.citation.label()


def hit_source_line(hit: Hit) -> str:
    c = hit.citation
    page = f"p.{c.page}" if c.page is not None else "p.—"
    return f"{c.path}:{c.line_start} ({page})"


def ensure_ordered(hits: Sequence[Hit]) -> None:
    """防御性断言：存储侧返回的就是最终顺序，rank 必须从 1 连续递增。"""
    for expected, hit in enumerate(hits, start=1):
        if hit.rank != expected:
            raise ValueError(f"结果顺序被破坏：第 {expected} 条的 rank={hit.rank}")


def no_result_message(query: Query) -> str:
    scope = []
    if query.filters.course:
        scope.append(f"课程={query.filters.course}")
    if query.filters.max_lecture is not None:
        scope.append(f"讲次≤{query.filters.max_lecture}")
    suffix = "（过滤：" + "，".join(scope) + "）" if scope else ""
    return f"没有检索到与「{query.text}」相关的段落{suffix}。换个说法，或去掉过滤条件。"


def format_hits_table(response: SearchResponse) -> str:
    """CLI 表格：序号 | 出处 | 命中通道 | 相关度 | 片段。"""
    if not response.hits:
        return no_result_message(Query(text=response.query, filters=response.filters))
    lines = [
        f"查询：{response.query}    （{response.store_mode}，{response.elapsed_ms} ms）",
        "",
    ]
    for hit in response.hits:
        lines.append(f"[{hit.rank}] {render_citation(hit)}")
        lines.append(f"    通道 {','.join(hit.channels) or '-'}   score {hit.score:.4f}   原文 {hit_source_line(hit)}")
        lines.append(f"    {hit.snippet}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def hits_as_rows(hits: Iterable[Hit]) -> list[dict]:
    return [h.to_dict(include_text=False) for h in hits]
