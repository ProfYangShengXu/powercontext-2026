"""来源反查：路径存在、行号在范围、内容 hash 对得上、库里查得到。"""

from __future__ import annotations

from typing import Callable

from mmctx.core.chunking import split_lines
from mmctx.core.hashing import content_hash
from mmctx.ports.store import Hit


class VerifyError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors[:5]))
        self.errors = errors


def verify_sources(
    pack: dict[str, object],
    read_source: Callable[[str], str],
    lookup: Callable[[str], Hit | None] | None = None,
) -> list[str]:
    """逐条反查；返回问题列表（空列表 = 全通过），不在这里决定退出码。"""
    errors: list[str] = []
    sources = pack.get("sources") or []
    if not sources:
        errors.append("context pack has no sources")

    for source in sources:
        path = str(source["path"])
        uid = str(source["chunk_uid"])
        start = int(source["start_line"])
        end = int(source["end_line"])
        expected = str(source["content_hash"])
        try:
            text = read_source(path)
        except FileNotFoundError:
            errors.append(f"{path}: file not found")
            continue
        lines = split_lines(text)
        if start < 1 or end < start:
            errors.append(f"{path}:{start}-{end}: invalid line range")
            continue
        if end > len(lines):
            errors.append(f"{path}:{start}-{end}: end_line beyond file ({len(lines)} lines)")
            continue
        actual = "\n".join(lines[start - 1 : end])
        if content_hash(actual) != expected:
            errors.append(f"{path}:{start}-{end}: content hash mismatch")
            continue
        if actual != source["content"]:
            errors.append(f"{path}:{start}-{end}: stored content differs from file slice")
            continue
        if lookup is not None:
            hit = lookup(uid)
            if hit is None:
                errors.append(f"{uid}: chunk not found in database (context must come from SQL)")
            elif hit.content_hash != expected:
                errors.append(f"{uid}: database content hash mismatch")
    return errors


def require_verified(
    pack: dict[str, object],
    read_source: Callable[[str], str],
    lookup: Callable[[str], Hit | None] | None = None,
) -> None:
    errors = verify_sources(pack, read_source, lookup)
    if errors:
        raise VerifyError(errors)
