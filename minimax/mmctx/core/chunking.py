"""切块规则：规格按 ## 条目，代码按顶层 def/class，测试按 test_ 函数。

行号一律 1-based 闭区间。content 用同一套规则从原文重建，因此
path:start-end 与 content_hash 可以被独立反查。
"""

from __future__ import annotations

import re

from mmctx.ports.store import Chunk

_SPEC_HEADING = re.compile(r"^##\s+")
_CODE_DEF = re.compile(r"^(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
_TEST_DEF = re.compile(r"^(?:async\s+)?def\s+(test_[A-Za-z0-9_]*)|^class\s+(Test[A-Za-z0-9_]*)")


def split_lines(text: str) -> list[str]:
    """按 \\n 切行；末尾换行不产生空行。"""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _symbol_for(kind: str, line: str) -> str:
    if kind == "spec":
        return line.lstrip("# ").strip()
    match = _CODE_DEF.match(line) or _TEST_DEF.match(line)
    return match.group(1) if match else ""


def _boundaries(kind: str, lines: list[str]) -> list[int]:
    if kind == "spec":
        return [i for i, line in enumerate(lines) if _SPEC_HEADING.match(line)]
    if kind == "code":
        return [i for i, line in enumerate(lines) if _CODE_DEF.match(line)]
    if kind == "test":
        return [i for i, line in enumerate(lines) if _TEST_DEF.match(line)]
    raise ValueError(f"unknown doc kind: {kind}")


def chunk_text(kind: str, path: str, text: str, doc_id: str) -> list[Chunk]:
    """把一份文件切成若干块。空文件返回空列表。"""
    if kind not in ("spec", "code", "test"):
        raise ValueError(f"unknown doc kind: {kind}")
    lines = split_lines(text)
    if not lines:
        return []

    starts = _boundaries(kind, lines)
    if not starts:
        starts = [0]
    if starts[0] != 0:
        starts = [0, *starts]

    chunks: list[Chunk] = []
    for ordinal, start in enumerate(starts):
        end = starts[ordinal + 1] if ordinal + 1 < len(starts) else len(lines)
        # 去掉块尾的空行，避免块之间互相包含空白；content 仍然可由
        # start_line..end_line 精确重建（下面按去尾后的 end 记）。
        while end > start + 1 and lines[end - 1].strip() == "":
            end -= 1
        content = "\n".join(lines[start:end])
        if not content.strip():
            continue
        from mmctx.core.hashing import content_hash

        chunks.append(
            Chunk(
                doc_id=doc_id,
                ordinal=len(chunks),
                kind=kind,
                path=path,
                start_line=start + 1,
                end_line=end,
                content=content,
                content_hash=content_hash(content),
                symbol=_symbol_for(kind, lines[start]),
            )
        )
    return chunks
