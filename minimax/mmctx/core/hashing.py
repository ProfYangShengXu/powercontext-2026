"""确定性的身份与摘要计算。只依赖标准库。"""

from __future__ import annotations

import hashlib
from typing import Iterable


def sha256_hex(text: str, length: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def doc_id_for(path: str) -> str:
    """文件身份只由相对路径决定；路径变即新文档。"""
    return sha256_hex(path)


def content_hash(text: str) -> str:
    """内容 hash 是「规格是否更新」的唯一判据，不看 mtime、不看版本号。"""
    return sha256_hex(text)


def corpus_digest(items: Iterable[tuple[str, str]]) -> str:
    """按路径排序后对 (path, content_hash) 求摘要。"""
    payload = "\n".join(f"{path}\0{content_hash(text)}" for path, text in sorted(items))
    return sha256_hex(payload, 32)


def spec_digest(items: Iterable[tuple[str, str]]) -> str:
    """只覆盖规格文件的摘要，写进上下文包与任务状态。"""
    return corpus_digest(items)
