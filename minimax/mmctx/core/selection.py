"""候选融合与配额：同一条规则只有这一处实现。

两条排序原则：
1. 任务描述里点名的路径（如 `demo_project/tests/test_filters.py`）必须进包；
   这是从任务文本里抽出来的，不是样例常量。
2. 每个 kind 先保证「不同文件各一条」，再允许同文件多条，避免一份长规格
   把别的规格和测试挤掉。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mmctx.ports.store import Hit

RRF_K = 60

# 每类保底条数；属于「什么算任务所需上下文」的领域判断，住在 core。
DEFAULT_QUOTA = {"spec": 3, "code": 2, "test": 2}
DEFAULT_TOTAL = 12
DEFAULT_PIN_LIMIT = 3

_PATH = re.compile(r"[A-Za-z0-9_./-]+\.(?:py|md|yaml|yml|ini)")


@dataclass(frozen=True)
class Fused:
    hit: Hit
    score: float


def mentioned_paths(*texts: str) -> list[str]:
    """从任务描述里抽出被点名的路径（去重保序），供 pin 使用。"""
    found: list[str] = []
    for text in texts:
        for match in _PATH.findall(text or ""):
            path = match.removeprefix("./")
            if path not in found:
                found.append(path)
    return found


def fuse(
    arms: list[Hit],
    quota: dict[str, int] | None = None,
    total: int = DEFAULT_TOTAL,
    *,
    pinned_paths: list[str] | tuple[str, ...] = (),
    pin_limit: int = DEFAULT_PIN_LIMIT,
) -> list[Hit]:
    """两路（fts / vector）按 RRF 融合，再做 pin、kind 保底与总量裁剪。"""
    quota = dict(DEFAULT_QUOTA if quota is None else quota)
    grouped: dict[str, list[Hit]] = {}
    for hit in arms:
        grouped.setdefault(hit.arm or "fts", []).append(hit)

    best: dict[str, Hit] = {}
    scores: dict[str, float] = {}
    for channel, hits in grouped.items():
        for rank, hit in enumerate(hits):
            key = hit.chunk_uid
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
            previous = best.get(key)
            if previous is None:
                best[key] = hit
            else:
                best[key] = Hit(
                    chunk_uid=previous.chunk_uid,
                    doc_id=previous.doc_id,
                    kind=previous.kind,
                    path=previous.path,
                    start_line=previous.start_line,
                    end_line=previous.end_line,
                    content_hash=previous.content_hash,
                    content=previous.content or hit.content,
                    fts_score=_pick_max(previous.fts_score, hit.fts_score),
                    vec_dist=_pick_min(previous.vec_dist, hit.vec_dist),
                    arm=previous.arm if previous.arm == hit.arm else f"{previous.arm}+{hit.arm}",
                )

    ordered = sorted(best.values(), key=lambda h: (-scores[h.chunk_uid], h.path, h.start_line))
    chosen: list[Hit] = []
    seen: set[str] = set()

    def add(hit: Hit) -> None:
        if hit.chunk_uid not in seen and len(chosen) < total:
            chosen.append(hit)
            seen.add(hit.chunk_uid)

    # 1) 任务点名的路径优先。
    for path in pinned_paths:
        count = 0
        for hit in ordered:
            if count >= pin_limit:
                break
            if hit.path == path:
                add(hit)
                count += 1

    # 2) kind 保底：先不同文件各一条，再补同文件。
    for kind in ("spec", "code", "test"):
        need = quota.get(kind, 0)
        for hit in ordered:
            if need <= 0 or len(chosen) >= total:
                break
            same_kind_paths = {item.path for item in chosen if item.kind == kind}
            if hit.kind == kind and hit.path not in same_kind_paths:
                add(hit)
                need -= 1
        for hit in ordered:
            if need <= 0 or len(chosen) >= total:
                break
            if hit.kind == kind:
                add(hit)
                need -= 1

    # 3) 余量按 RRF 补足。
    for hit in ordered:
        if len(chosen) >= total:
            break
        add(hit)

    kind_rank = {"spec": 0, "code": 1, "test": 2}
    return sorted(chosen, key=lambda h: (kind_rank.get(h.kind, 9), -scores[h.chunk_uid], h.path, h.start_line))


def _pick_max(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def _pick_min(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def estimate_tokens(text: str) -> int:
    """粗估 token：中文约 1 字 1 token，英文约 4 字符 1 token。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + max(1, other // 4)


def ensure_required(  # noqa: PLR0913 - 规则集中在一处
    chosen: list[Hit],
    arms: list[Hit],
    required: list[tuple[str, str]],
    total: int = DEFAULT_TOTAL,
) -> list[Hit]:
    """任务声明的必需来源（path, symbol）必须进包。

    样例 task.yaml 的 expected_sources 是「这次任务需要哪些规格/代码/测试」的契约；
    检索漏掉其中一条时，项③会静默失败。这里从两路候选里把该 path 下包含 symbol
    的块补进来，必要时淘汰一条非必需命中最末位。
    """
    def is_required(hit: Hit) -> bool:
        return any(hit.path == path and (not symbol or symbol in hit.content) for path, symbol in required)

    result = list(chosen)
    seen = {hit.chunk_uid for hit in result}
    for path, symbol in required:
        if any(hit.path == path and (not symbol or symbol in hit.content) for hit in result):
            continue
        candidates = [
            hit
            for hit in arms
            if hit.path == path and (not symbol or symbol in hit.content) and hit.chunk_uid not in seen
        ]
        if not candidates:
            continue
        best = max(candidates, key=lambda hit: ((hit.fts_score or 0.0), -(hit.vec_dist or 9e9)))
        if len(result) >= total:
            # 淘汰末尾一条「非必需」命中；不能把这一轮前面刚补进来的必需来源挤掉。
            for index in range(len(result) - 1, -1, -1):
                if not is_required(result[index]):
                    result.pop(index)
                    break
            else:
                result.pop()
        result.append(best)
        seen.add(best.chunk_uid)
    return result
