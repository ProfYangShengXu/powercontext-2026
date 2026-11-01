"""Minimal PowerContext HTTP client built on the Python standard library.

The sidecar deliberately depends on nothing outside the standard library: it
speaks PowerContext's documented HTTP API with ``urllib`` so that the demo can
run on a laptop with no extra packages installed.

API contract (PowerContext 1.2.0): every memory call is ``POST`` and carries an
explicit ``scope_id`` in the JSON body.  There is no implicit session scope.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class PowerContextError(Exception):
    """A failed PowerContext call with enough context to classify it."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        request_id: str | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id

    @property
    def reachable(self) -> bool:
        return self.status != 0


class PowerContextClient:
    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # -- transport ---------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                request_id = response.headers.get("X-PowerContext-Request-ID")
                body = _decode(raw)
                body.setdefault("_request_id", request_id)
                return body
        except urllib.error.HTTPError as error:
            raw = error.read()
            request_id = error.headers.get("X-PowerContext-Request-ID")
            error_body = _decode(raw)
            error_info = error_body.get("error") if isinstance(error_body, dict) else None
            if isinstance(error_info, dict):
                code = str(error_info.get("code") or "powercontext_error")
                message = str(error_info.get("message") or "")
            else:
                code = f"http_{error.code}"
                message = raw[:300].decode("utf-8", "replace")
            raise PowerContextError(
                error.code,
                code,
                message,
                request_id=request_id,
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise PowerContextError(
                0,
                "powercontext_unreachable",
                f"cannot reach PowerContext at {self.base_url}: {reason}",
            ) from error

    # -- operations --------------------------------------------------------
    def readiness(self) -> dict[str, Any]:
        url = f"{self.base_url}/health/ready"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                raw = response.read()
                return {"status": response.status, "body": _decode(raw)}
        except urllib.error.HTTPError as error:
            return {
                "status": error.code,
                "body": _decode(error.read()),
            }
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            return {"status": 0, "body": {"error": str(reason)}}

    def create_scope(self, title: str, summary: str, idempotency_key: str) -> str:
        result = self._request(
            "POST",
            "/v1/scopes",
            {"title": title, "summary": summary, "idempotency_key": idempotency_key},
        )
        scope_id = result.get("scope_id")
        if not scope_id:
            raise PowerContextError(0, "bad_scope_response", "scope response has no scope_id")
        return str(scope_id)

    def remember(self, scope_id: str, kind: str, text: str, reason: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"scope_id": scope_id, "kind": kind, "text": text}
        if reason:
            payload["reason"] = reason
        return self._request("POST", "/v1/memory/remember", payload)

    def search(self, scope_id: str, query: str, limit: int = 8) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/memory/search",
            {"scope_id": scope_id, "query": query, "limit": limit, "mode": "auto"},
        )

    def list_entries(self, scope_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/memory/entries/list",
            {"scope_id": scope_id, "include_inactive": False},
        )

    def list_changes(self, scope_id: str, since_revision: int = 0) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/memory/changes",
            {"scope_id": scope_id, "since_revision": since_revision},
        )


def _decode(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"raw": raw[:500].decode("utf-8", "replace")}
    return value if isinstance(value, dict) else {"value": value}
