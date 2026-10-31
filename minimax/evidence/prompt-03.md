# 交给 MiniMax-Text-01 的第 3 轮修正

第 2 轮通过 9/10，仍剩 1 个失败。请只修这一处，输出**完整文件**，只输出一个 ```python 代码块，不要解释。

## 失败：HTTP 200 且 body 不是 dict 时抛 AttributeError

```
tests/test_minimax_payload.py::test_non_dict_body_is_bad_response
    with pytest.raises(PayloadError) as caught:
        parse_response(200, None, raw_text="<html>oops</html>")
>       base_resp = body.get("base_resp", {})
E       AttributeError: 'NoneType' object has no attribute 'get'
```

第 2 轮在把 `http_status != 200` 提前后，把「HTTP 200 + body 非 dict」的分支弄丢了。请在
`http_status != 200` 的处理块**之后**、读取 `base_resp` **之前**加入：

```python
    if not isinstance(body, dict):
        raise PayloadError(
            message=f"Bad response: http_status={http_status}, raw_text={raw_text[:200]}",
            code="bad_response",
            retryable=False,
            status_code=http_status,
        )
```

另外把 `base_resp` 的取值改成对 None 容错：
`base_resp = body.get("base_resp") or {}`，并且 `if not isinstance(base_resp, dict): base_resp = {}`。

其余逻辑保持不变。上一版实现：

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedResponse:
    text: str
    finish_reason: str
    usage: dict[str, object]
    model: str
    request_id: str
    http_status: int


class PayloadError(Exception):
    def __init__(self, message: str, *, code: str, retryable: bool, status_code: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code

    def __repr__(self):
        return f"PayloadError(code={self.code!r}, retryable={self.retryable}, status_code={self.status_code}, message={self.args[0]!r})"


def parse_response(
    http_status: int,
    body: object,
    *,
    raw_text: str = "",
    request_id: str = "",
    fallback_model: str = "",
) -> ParsedResponse:
    # Step 1: Handle non-200 HTTP status codes
    if http_status != 200:
        retryable = http_status in {408, 409, 429, 500, 502, 503, 504}
        if isinstance(body, dict):
            if "error" in body and isinstance(body["error"], dict):
                error_code = str(body["error"].get("code", f"http_{http_status}"))
                error_message = body["error"].get("message", str(body["error"]))
            elif "base_resp" in body and isinstance(body["base_resp"], dict) and "status_msg" in body["base_resp"]:
                error_message = body["base_resp"]["status_msg"]
                error_code = "base_resp_error"
            else:
                error_message = raw_text[:300] if raw_text else repr(body)
                error_code = f"http_{http_status}"
        else:
            error_message = raw_text[:300] if raw_text else repr(body)
            error_code = f"http_{http_status}"
        raise PayloadError(
            message=error_message,
            code=error_code,
            retryable=retryable,
            status_code=http_status,
        )

    # Step 2: Handle base_resp status code
    base_resp = body.get("base_resp", {})
    sc = int(base_resp.get("status_code", 0) or 0)
    if sc != 0:
        code = f"minimax_{sc}"
        retryable = sc in {1000, 1001, 1002, 1024, 1033, 2056, 2062, 2064, 2066}
        message = base_resp.get("status_msg", f"MiniMax business error {sc}")
        raise PayloadError(
            message=message,
            code=code,
            retryable=retryable,
            status_code=200,
        )

    # Step 3: Check if choices is empty
    choices = body.get("choices", [])
    if not choices:
        raise PayloadError(
            message="Empty choices",
            code="empty_choices",
            retryable=True,
            status_code=200,
        )

    # Step 4: Extract choice details
    choice = choices[0]
    message = choice.get("message", {})
    content = str(message.get("content", ""))
    finish_reason = str(choice.get("finish_reason", ""))

    # Step 5: Check if content is empty
    if not content.strip():
        raise PayloadError(
            message=f"Empty content, finish_reason={finish_reason}, possibly due to thinking token limit",
            code="empty_content",
            retryable=True,
            status_code=200,
        )

    # Step 6: Successful parsing
    usage = body.get("usage", {})
    if not isinstance(usage, dict):
        usage = {}
    model = str(body.get("model", fallback_model) or fallback_model)
    rid = request_id or str(body.get("id", ""))
    return ParsedResponse(
        text=content,
        finish_reason=finish_reason,
        usage=usage,
        model=model,
        request_id=rid,
        http_status=http_status,
    )
```
