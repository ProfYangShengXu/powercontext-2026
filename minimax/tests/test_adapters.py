"""适配器单元测试：本地嵌入的确定性与状态上限的服务端口径。"""

from __future__ import annotations

import pytest

from mmctx.adapters.embedding import LocalHashedEmbedding
from mmctx.adapters.pc_state import TEXT_LIMIT_BYTES, check_text_limit
from mmctx.ports.state import StateTooLargeError


def test_embedding_is_384_dimensional_and_deterministic():
    encoder = LocalHashedEmbedding(384)
    first = encoder.encode_one("订单筛选 按状态 filtering")
    again = encoder.encode_one("订单筛选 按状态 filtering")
    assert len(first) == 384
    assert first == again
    assert abs(sum(value * value for value in first) - 1.0) < 1e-6


def test_embedding_separates_different_text():
    encoder = LocalHashedEmbedding(384)
    a = encoder.encode_one("订单筛选")
    b = encoder.encode_one("会员折扣")
    assert a != b


def test_state_limit_matches_server_utf8_bytes():
    assert check_text_limit("a" * TEXT_LIMIT_BYTES) == TEXT_LIMIT_BYTES
    with pytest.raises(StateTooLargeError):
        check_text_limit("a" * (TEXT_LIMIT_BYTES + 1))
    # 8192 是 NFC + strip 之后的 UTF-8 字节数：2730 个汉字 = 8190 字节。
    assert check_text_limit("中" * 2730) == 8190
    with pytest.raises(StateTooLargeError):
        check_text_limit("中" * 2731)
    # 组合字符会被 NFC 归一化，字节数按归一化后计算。
    assert check_text_limit("e\u0301") == len("é".encode("utf-8"))
