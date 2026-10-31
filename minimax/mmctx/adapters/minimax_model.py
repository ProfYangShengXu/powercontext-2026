"""ModelPort 的 MiniMax 实现：国内站 text/chatcompletion_v2。

响应解析由 ``minimax_payload.parse_response`` 完成——该模块由 MiniMax-Text-01
生成，见 ``evidence/``（请求体、响应原文、落盘文件、对应提交）。

注意两点（实测）：
- 该端点在业务错误时仍返回 HTTP 200，错误在 ``base_resp.status_code``；
- thinking token 计入输出上限，上限太小会 finish_reason=length 且 content 为空。
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from mmctx.adapters.minimax_payload import ParsedResponse, PayloadError, parse_response
from mmctx.ports.model import ModelError, ModelInfo, ModelReply, ModelRequest


class MiniMaxModel:
    """MiniMax chatcompletion_v2 适配器。

    连接信息全部由装配点注入；本类不读环境变量。
    """

    def __init__(
        self,
        api_key: str | None,
        base_url: str,
        model: str,
        *,
        chat_path: str = "/text/chatcompletion_v2",
        timeout_s: float = 60.0,
        retries: int = 2,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.chat_path = chat_path if chat_path.startswith("/") else "/" + chat_path
        self.timeout_s = timeout_s
        self.retries = retries
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout_s)

    def info(self) -> ModelInfo:
        return ModelInfo(model=self.model, base_url=self.base_url)

    def close(self) -> None:
        self._client.close()

    def complete(self, request: ModelRequest) -> ModelReply:
        if not self.api_key:
            raise ModelError("缺少 MINIMAX_API_KEY：请在 .env 里配置后重试", code="missing_api_key")
        last_error: ModelError | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._once(request)
            except ModelError as error:
                last_error = error
                if not error.retryable or attempt >= self.retries:
                    raise
                time.sleep(float(attempt + 1))
        raise last_error or ModelError("model call failed")

    def _once(self, request: ModelRequest) -> ModelReply:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": unit.role, "content": unit.text} for unit in request.units],
            "stream": False,
            "max_tokens": int(request.output_allowance),
        }
        started = time.perf_counter()
        try:
            response = self._client.post(
                self.chat_path,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
            )
        except httpx.HTTPError as error:
            raise ModelError(f"请求 MiniMax 失败：{error}", code="network_error", retryable=True) from error
        latency_ms = int((time.perf_counter() - started) * 1000)

        raw_text = response.text
        try:
            body: object = response.json()
        except ValueError:
            body = None

        try:
            parsed: ParsedResponse = parse_response(
                response.status_code,
                body,
                raw_text=raw_text,
                request_id=response.headers.get("x-request-id") or "",
                fallback_model=self.model,
            )
        except PayloadError as error:
            raise ModelError(
                f"MiniMax 调用失败（{error.code}，HTTP {error.status_code}）：{error}",
                code=error.code,
                retryable=error.retryable,
            ) from error

        return ModelReply(
            text=parsed.text,
            finish_reason=parsed.finish_reason,
            usage=parsed.usage,
            latency_ms=latency_ms,
            request_id=parsed.request_id,
            model=parsed.model,
        )
