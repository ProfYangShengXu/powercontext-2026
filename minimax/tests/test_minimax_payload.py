"""MiniMax v2 响应解析的契约测试。

mmctx/adapters/minimax_payload.py 由 MiniMax-Text-01 生成（见 evidence/），
本文件是它落地前的验收契约：成功路径与所有错误分类都必须可区分。
"""

from __future__ import annotations

import pytest

from mmctx.adapters.minimax_payload import ParsedResponse, PayloadError, parse_response


def _ok_body(content: str = "hello", finish: str = "stop") -> dict:
    return {
        "id": "req-123",
        "model": "MiniMax-Text-01",
        "choices": [{"finish_reason": finish, "index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"total_tokens": 42},
        "base_resp": {"status_code": 0, "status_msg": ""},
    }


def test_success_returns_every_field():
    parsed = parse_response(200, _ok_body(), request_id="hdr-1")
    assert isinstance(parsed, ParsedResponse)
    assert parsed.text == "hello"
    assert parsed.finish_reason == "stop"
    assert parsed.usage == {"total_tokens": 42}
    assert parsed.model == "MiniMax-Text-01"
    assert parsed.request_id == "hdr-1"
    assert parsed.http_status == 200


def test_request_id_falls_back_to_body_id_and_model_to_fallback():
    parsed = parse_response(200, _ok_body())
    assert parsed.request_id == "req-123"
    body = _ok_body()
    body["model"] = ""
    assert parse_response(200, body, fallback_model="MiniMax-Text-01").model == "MiniMax-Text-01"


def test_non_dict_body_is_bad_response():
    with pytest.raises(PayloadError) as caught:
        parse_response(200, None, raw_text="<html>oops</html>")
    error = caught.value
    assert error.code == "bad_response"
    assert error.status_code == 200
    assert "oops" in str(error)


def test_http_error_is_not_retryable_for_auth():
    body = {"error": {"code": "401", "message": "invalid api key"}}
    with pytest.raises(PayloadError) as caught:
        parse_response(401, body)
    error = caught.value
    assert error.code == "401"
    assert error.retryable is False
    assert error.status_code == 401


def test_http_5xx_is_retryable():
    with pytest.raises(PayloadError) as caught:
        parse_response(503, None, raw_text="upstream unavailable")
    assert caught.value.code == "http_503"
    assert caught.value.retryable is True


def test_business_error_1002_rate_limit_is_retryable():
    body = {"base_resp": {"status_code": 1002, "status_msg": "rate limit"}}
    with pytest.raises(PayloadError) as caught:
        parse_response(200, body)
    assert caught.value.code == "minimax_1002"
    assert caught.value.retryable is True
    assert "rate limit" in str(caught.value)


def test_business_error_2013_invalid_params_is_not_retryable():
    body = {"base_resp": {"status_code": 2013, "status_msg": "unknown model"}}
    with pytest.raises(PayloadError) as caught:
        parse_response(200, body)
    assert caught.value.code == "minimax_2013"
    assert caught.value.retryable is False


def test_empty_choices_is_retryable():
    with pytest.raises(PayloadError) as caught:
        parse_response(200, {"choices": [], "base_resp": {"status_code": 0}})
    assert caught.value.code == "empty_choices"
    assert caught.value.retryable is True


def test_empty_content_reports_finish_reason():
    body = _ok_body(content="   ", finish="length")
    with pytest.raises(PayloadError) as caught:
        parse_response(200, body)
    assert caught.value.code == "empty_content"
    assert caught.value.retryable is True
    assert "length" in str(caught.value)


def test_bad_usage_and_string_status_codes_are_tolerated():
    body = _ok_body()
    body["usage"] = None
    body["base_resp"] = {"status_code": "0"}
    parsed = parse_response(200, body)
    assert parsed.usage == {}

    bad = {"base_resp": {"status_code": "1004", "status_msg": "auth"}}
    with pytest.raises(PayloadError) as caught:
        parse_response(200, bad)
    assert caught.value.code == "minimax_1004"
    assert caught.value.retryable is False
