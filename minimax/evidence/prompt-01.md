# 交给 MiniMax-Text-01 的生成任务（第 1 轮）

模块：`minimax/mmctx/adapters/minimax_payload.py`
目标：把 MiniMax `chatcompletion_v2` 的 HTTP 响应解析成结构化结果，或抛出可分类的 `PayloadError`。

---

请只输出一个 ```python 代码块，内容是**完整文件**，不要解释、不要举例、不要额外文字。

## 接口

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
        ...
    # 必须有可读属性：code: str、retryable: bool、status_code: int


def parse_response(
    http_status: int,
    body: object,
    *,
    raw_text: str = "",
    request_id: str = "",
    fallback_model: str = "",
) -> ParsedResponse:
    ...
```

## 解析规则（严格按顺序执行）

1. `body` 不是 dict（None / list / str / int 等）→ 抛 `PayloadError(code="bad_response", retryable=(http_status >= 500 or http_status == 0), status_code=http_status)`，消息里包含 `http_status` 与 `raw_text` 前 200 字符。
2. `http_status != 200`：
   - `retryable = http_status in {408, 409, 429, 500, 502, 503, 504}`；
   - 若 `body["error"]` 是 dict：`code = str(error.get("code") or f"http_{http_status}")`，消息取 `error.get("message")` 或 `str(error)`；
   - 否则若 `body["base_resp"]` 是 dict 且有 `status_msg`：消息取它；
   - 否则消息取 `raw_text[:300]`，没有就取 `repr(body)`；
   - 抛 `PayloadError`，`status_code=http_status`。
3. `base_resp = body.get("base_resp") or {}`，`sc = int(base_resp.get("status_code", 0) or 0)`（注意可能是字符串）。
   若 `sc != 0`：`code=f"minimax_{sc}"`，`retryable = sc in {1000, 1001, 1002, 1024, 1033, 2056, 2062, 2064, 2066}`，消息取 `base_resp.get("status_msg")` 或 `f"MiniMax business error {sc}"`，`status_code=200`。
4. `choices = body.get("choices") or []`；为空 → `PayloadError(code="empty_choices", retryable=True, status_code=200)`。
5. `choice = choices[0]`；`message = choice.get("message") or {}`；`content = str(message.get("content") or "")`；`finish_reason = str(choice.get("finish_reason") or "")`。
6. `content.strip()` 为空 → `PayloadError(code="empty_content", retryable=True, status_code=200)`，消息中包含 `finish_reason`，并提示 thinking token 可能吃掉了输出上限。
7. 成功：`usage = body.get("usage")`，不是 dict 就用 `{}`；`model = str(body.get("model") or fallback_model)`；`rid = request_id or str(body.get("id") or "")`；返回 `ParsedResponse(content, finish_reason, usage, model, rid, http_status)`。

## 边界要求

- `http_status` 与 `base_resp.status_code` 可能是字符串，转 int 要容错；
- `content`、`status_msg`、`message`、`usage` 可能是 `None`；
- 只允许标准库（`dataclasses` / `typing`），不得 import 项目内其他模块；
- 不要在文件里写 `print`、`logging`、网络请求或文件读写。

## 样例（用于自查，不要输出）

输入 `(200, {"id": "req-123", "model": "MiniMax-Text-01", "choices": [{"finish_reason": "stop", "message": {"content": "hello"}}], "usage": {"total_tokens": 42}, "base_resp": {"status_code": 0}}, request_id="hdr-1")`
→ `ParsedResponse("hello", "stop", {"total_tokens": 42}, "MiniMax-Text-01", "hdr-1", 200)`

输入 `(200, {"base_resp": {"status_code": 1002, "status_msg": "rate limit"}})`
→ `PayloadError(code="minimax_1002", retryable=True, status_code=200)`

输入 `(401, {"error": {"code": "401", "message": "invalid api key"}})`
→ `PayloadError(code="401", retryable=False, status_code=401)`
