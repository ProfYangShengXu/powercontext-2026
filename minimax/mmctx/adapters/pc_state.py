"""StatePort 的 PowerContext 实现：Basic Memory 显式 remember / entries.list。"""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Any

import httpx

from mmctx.ports.state import StateRecord, StateRef, StateTooLargeError

# PowerContext 的实现约束：memory/remember 的 text 在 NFC + strip 之后不得超过
# 8192 UTF-8 字节。这是适配器内部校验，端口签名不出现字节数。
TEXT_LIMIT_BYTES = 8192
SOURCE_CONTENT_LIMIT_CHARS = 200_000

STATE_KIND = "mmtask"


class PCUnavailableError(RuntimeError):
    pass


class PCError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def check_text_limit(text: str, limit: int = TEXT_LIMIT_BYTES) -> int:
    """按服务端口径（NFC + strip）计算字节数；超限抛 StateTooLargeError。"""
    normalized = unicodedata.normalize("NFC", text).strip()
    size = len(normalized.encode("utf-8"))
    if size > limit:
        raise StateTooLargeError(size, limit)
    return size


class PowerContextState:
    def __init__(
        self,
        base_url: str,
        scope_title: str,
        *,
        scope_id: str | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.scope_title = scope_title
        self._scope_id = scope_id
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout_s)

    # -- 基础设施 ---------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
        try:
            response = self._client.request(method, path, json=payload)
        except httpx.HTTPError as error:
            raise PCUnavailableError(
                f"PowerContext 不可达（{self.base_url}）：{error}。请先启动 powercontext server"
            ) from error
        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {"raw": response.text[:500]}
        return response.status_code, body

    def health(self) -> dict[str, object]:
        status, body = self._request("GET", "/health/ready")
        if status != 200:
            raise PCUnavailableError(f"PowerContext 未就绪：HTTP {status} {body}")
        return body if isinstance(body, dict) else {"status": "ready"}

    def ensure_scope(self) -> str:
        if self._scope_id:
            status, body = self._request("GET", f"/v1/scopes/{self._scope_id}")
            if status == 200:
                return self._scope_id
            raise PCError(f"PC_SCOPE_ID 不存在：{self._scope_id}", status=status)
        status, body = self._request("GET", f"/v1/scopes?query={self.scope_title}&limit=50")
        if status == 200 and isinstance(body, dict):
            for item in body.get("items", []):
                if item.get("title") == self.scope_title:
                    self._scope_id = str(item["scope_id"])
                    return self._scope_id
        payload = {
            "title": self.scope_title,
            "summary": "MiniMax SDD 上下文管理：任务状态与工程资料索引",
            "idempotency_key": hashlib.sha256(self.scope_title.encode("utf-8")).hexdigest(),
        }
        status, body = self._request("POST", "/v1/scopes", payload)
        if status == 201 and isinstance(body, dict):
            self._scope_id = str(body["scope_id"])
            return self._scope_id
        if status == 409:
            status, body = self._request("GET", f"/v1/scopes?query={self.scope_title}&limit=50")
            for item in body.get("items", []):
                if item.get("title") == self.scope_title:
                    self._scope_id = str(item["scope_id"])
                    return self._scope_id
        raise PCError(f"创建 Scope 失败：HTTP {status} {body}", status=status)

    # -- 资料来源（证明资料进过 PowerContext）-----------------------------
    def capture_source(self, source_id: str, content: str) -> str:
        if not content.strip():
            raise PCError("source content must not be empty")
        if len(content) > SOURCE_CONTENT_LIMIT_CHARS:
            raise PCError(f"source content is {len(content)} chars, limit is {SOURCE_CONTENT_LIMIT_CHARS}")
        scope_id = self.ensure_scope()
        status, body = self._request(
            "POST", "/v1/sources/content", {"scope_id": scope_id, "source_id": source_id, "content": content}
        )
        if status in (200, 202):
            return str(body.get("source", {}).get("source_id", source_id))
        raise PCError(f"注册 Source 失败（{source_id}）：HTTP {status} {body}", status=status)

    # -- 状态 -------------------------------------------------------------
    def _entries(self) -> list[dict[str, Any]]:
        scope_id = self.ensure_scope()
        status, body = self._request("POST", "/v1/memory/entries/list", {"scope_id": scope_id})
        if status == 404:
            return []
        if status != 200:
            raise PCError(f"读取记忆条目失败：HTTP {status} {body}", status=status)
        return [item for item in body.get("entries", []) if item.get("kind") == STATE_KIND]

    def _records(self, task_id: str | None = None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        from mmctx.core.state_model import load_state

        found: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for entry in self._entries():
            try:
                state = load_state(str(entry.get("text", "")))
            except (ValueError, TypeError):
                continue
            if task_id is not None and state.get("task_id") != task_id:
                continue
            found.append((entry, state))
        return found

    def save(self, task_id: str, state_text: str, *, seq: int, reason: str = "") -> StateRef:
        check_text_limit(state_text)
        scope_id = self.ensure_scope()

        from mmctx.core.state_model import state_hash

        existing = self._records(task_id)
        if existing:
            entry, state = max(existing, key=lambda pair: int(pair[1].get("seq", 0)))
            if state_hash(state) == state_hash(_parse(state_text)):
                citation = entry.get("citation", {})
                return StateRef(
                    entry_id=str(citation.get("entry_id", "")),
                    version=int(entry.get("version", 1)),
                    kind=STATE_KIND,
                    unchanged=True,
                )

        status, body = self._request(
            "POST",
            "/v1/memory/remember",
            {"scope_id": scope_id, "kind": STATE_KIND, "text": state_text, "reason": reason or "mmctx state"},
        )
        if status != 200:
            raise PCError(f"保存状态失败：HTTP {status} {body}", status=status)
        entry = body.get("entry") or {}
        citation = entry.get("citation", {}) if isinstance(entry, dict) else {}
        return StateRef(
            entry_id=str(citation.get("entry_id", "")),
            version=int(entry.get("version", 1)) if isinstance(entry, dict) else 1,
            kind=STATE_KIND,
        )

    def load_latest(self, task_id: str) -> StateRecord | None:
        records = self._records(task_id)
        if not records:
            return None
        entry, state = max(records, key=lambda pair: int(pair[1].get("seq", 0)))
        citation = entry.get("citation", {})
        return StateRecord(
            text=str(entry.get("text", "")),
            ref=StateRef(
                entry_id=str(citation.get("entry_id", "")),
                version=int(entry.get("version", 1)),
                kind=STATE_KIND,
            ),
            seq=int(state.get("seq", 0)),
        )

    def count_state_entries(self, task_id: str) -> int:
        """该 task 在 PowerContext 里实际有多少条 Memory 记录（不去重）。"""
        return len(self._records(task_id))

    def list_task_ids(self) -> list[str]:
        ids: list[str] = []
        for entry, state in self._records():
            task_id = str(state.get("task_id", ""))
            if task_id and task_id not in ids:
                ids.append(task_id)
        return ids


def _parse(text: str) -> dict[str, Any]:
    from mmctx.core.state_model import load_state

    return load_state(text)
