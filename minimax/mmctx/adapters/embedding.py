"""本地确定性嵌入：不下载模型、不调在线服务，保证 FTS 单路可用、向量只是辅助。

384 维特征哈希。中文取字符 bigram，英文取长度 ≥2 的单词，数字串单独成词。
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Sequence

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*|\d{1,}|[\u4e00-\u9fff]+")


def tokens(text: str) -> list[str]:
    result: list[str] = []
    for piece in _TOKEN.findall(text):
        if "\u4e00" <= piece[0] <= "\u9fff":
            if len(piece) == 1:
                result.append(piece)
            else:
                result.extend(piece[i : i + 2] for i in range(len(piece) - 1))
        else:
            result.append(piece.lower())
    return result


class LocalHashedEmbedding:
    """把文本投影到固定维度的单位向量。同一文本永远得到同一向量。"""

    def __init__(self, dimension: int = 384) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.dimension = dimension

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.encode_one(text) for text in texts]

    def encode_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]

    @staticmethod
    def as_literal(vector: Sequence[float]) -> str:
        """seekdb 接收的向量字面量：[v1,v2,...]。"""
        return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"
