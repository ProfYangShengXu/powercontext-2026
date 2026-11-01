"""Stdlib-only HTTP sidecar that exposes PowerContext memory to Dify.

Run:
    python3 sidecar/server.py --port 8787 --powercontext-url http://127.0.0.1:18080

Endpoints (all POST bodies are plain UTF-8 text, session goes in a header):
    GET  /health
    POST /memory/write    X-Session-Id: <id>   body: the event text
    POST /memory/search   X-Session-Id: <id>   body: the query
    POST /memory/list     X-Session-Id: <id>   body: ignored

The sidecar maps one Dify conversation (``sys.conversation_id``) to one
PowerContext scope.  It never fabricates memories: every response is derived
from a real PowerContext call, and PowerContext failures are surfaced as 4xx/5xx
so the Dify workflow fails instead of answering from thin air.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pc_client import PowerContextClient, PowerContextError  # noqa: E402

MAX_BODY_BYTES = 64 * 1024
MAX_MEMORY_BYTES = 8192  # PowerContext hard limit for one remembered text
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")

LOGGER = logging.getLogger("dify-tavern-sidecar")


class Sidecar:
    def __init__(
        self,
        powercontext_url: str,
        *,
        scope_prefix: str = "dify-tavern",
        memory_kind: str = "prank",
        timeout: float = 15.0,
    ) -> None:
        self.client = PowerContextClient(powercontext_url, timeout=timeout)
        self.scope_prefix = scope_prefix
        self.memory_kind = memory_kind
        self._scopes: dict[str, str] = {}
        self._lock = threading.Lock()

    # -- scope mapping -----------------------------------------------------
    def _digest(self, session_id: str) -> str:
        return hashlib.sha256(f"{self.scope_prefix}:{session_id}".encode("utf-8")).hexdigest()[:20]

    def scope_for(self, session_id: str) -> str:
        digest = self._digest(session_id)
        with self._lock:
            cached = self._scopes.get(digest)
        if cached:
            return cached
        scope_id = self.client.create_scope(
            title=f"{self.scope_prefix} {digest[:8]}",
            summary="Dify chatflow demo: a tavern NPC that remembers what you did to it.",
            idempotency_key=f"{self.scope_prefix}-{digest}",
        )
        with self._lock:
            self._scopes[digest] = scope_id
        LOGGER.info("scope ready session=%s scope=%s", _short(session_id), scope_id)
        return scope_id

    # -- operations --------------------------------------------------------
    def write(self, session_id: str, text: str) -> dict[str, Any]:
        text = _clean_text(text)
        if not text:
            raise SidecarError(400, "empty_text", "the event text is empty")
        if len(text.encode("utf-8")) > MAX_MEMORY_BYTES:
            raise SidecarError(413, "text_too_long", f"event text exceeds {MAX_MEMORY_BYTES} bytes")
        scope_id = self.scope_for(session_id)
        result = self.client.remember(scope_id, self.memory_kind, text, reason="dify-chatflow")
        entry = result.get("entry")
        memory = result.get("memory") or {}
        return {
            "ok": True,
            "scope_id": scope_id,
            "revision": memory.get("revision"),
            "entry_id": (entry or {}).get("citation", {}).get("entry_id") if entry else None,
            "duplicate": entry is None,
            "text": text,
        }

    def search(self, session_id: str, query: str) -> dict[str, Any]:
        query = " ".join(query.split())
        if not query:
            raise SidecarError(400, "empty_query", "the search query is empty")
        scope_id = self.scope_for(session_id)
        result = self.client.search(scope_id, query, limit=8)
        hits = [
            {
                "text": str(hit.get("text", "")),
                "score": hit.get("score"),
                "matched_by": hit.get("matched_by"),
            }
            for hit in (result.get("hits") or [])
        ]
        return {
            "ok": True,
            "scope_id": scope_id,
            "mode": result.get("mode"),
            "count": len(hits),
            "hits": hits,
        }

    def list_memories(self, session_id: str) -> dict[str, Any]:
        scope_id = self.scope_for(session_id)
        result = self.client.list_entries(scope_id)
        by_id = {
            entry["citation"]["entry_id"]: entry
            for entry in (result.get("entries") or [])
            if entry.get("citation", {}).get("entry_id")
        }
        # `/entries/list` has no documented ordering; the revision change log does.
        # Walk it oldest-first so the NPC's ledger is stable between runs.
        ordered_ids: list[str] = []
        try:
            changes = self.client.list_changes(scope_id, since_revision=0)
            for revision in changes.get("revisions") or []:
                for change in revision.get("changes") or []:
                    entry_id = change.get("entry_id")
                    if change.get("op") == "add" and entry_id in by_id and entry_id not in ordered_ids:
                        ordered_ids.append(entry_id)
        except PowerContextError:
            LOGGER.warning("memory/changes unavailable; falling back to list order scope=%s", scope_id)
        ordered_ids.extend(sorted(entry_id for entry_id in by_id if entry_id not in ordered_ids))
        memories = [
            {
                "text": str(by_id[entry_id].get("text", "")),
                "kind": by_id[entry_id].get("kind"),
                "entry_id": entry_id,
                "version": by_id[entry_id].get("version"),
                "state": by_id[entry_id].get("state"),
            }
            for entry_id in ordered_ids
        ]
        memory = result.get("memory") or {}
        return {
            "ok": True,
            "scope_id": scope_id,
            "revision": memory.get("revision"),
            "count": len(memories),
            "memories": memories,
        }

    def health(self) -> tuple[int, dict[str, Any]]:
        probe = self.client.readiness()
        ready = probe.get("status") == 200
        body: dict[str, Any] = {
            "ok": ready,
            "sidecar": "dify-tavern-sidecar",
            "powercontext": {
                "url": self.client.base_url,
                "status": probe.get("status"),
                "ready": ready,
            },
            "cached_scopes": len(self._scopes),
        }
        if not ready:
            body["error"] = {"code": "powercontext_not_ready", "message": "PowerContext /health/ready is not 200"}
        return (200 if ready else 503), body


class SidecarError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class Handler(BaseHTTPRequestHandler):
    server_version = "DifyTavernSidecar/1.0"
    sidecar: Sidecar

    def do_GET(self) -> None:  # noqa: N802
        started = time.monotonic()
        status, body = 404, {"ok": False, "error": {"code": "not_found", "message": self.path}}
        if self.path.split("?", 1)[0] in ("/", "/health"):
            try:
                status, body = self.sidecar.health()
            except Exception as error:  # noqa: BLE001 - health must always answer
                status, body = 503, {"ok": False, "error": {"code": "health_failed", "message": str(error)}}
        self._send(status, body, started)

    def do_POST(self) -> None:  # noqa: N802
        started = time.monotonic()
        path = self.path.split("?", 1)[0]
        try:
            session_id = self._session_id()
            raw = self._read_body()
            if path == "/memory/write":
                status, body = 200, self.sidecar.write(session_id, raw.decode("utf-8", "replace"))
            elif path == "/memory/search":
                status, body = 200, self.sidecar.search(session_id, raw.decode("utf-8", "replace"))
            elif path == "/memory/list":
                status, body = 200, self.sidecar.list_memories(session_id)
            else:
                status, body = 404, {"ok": False, "error": {"code": "not_found", "message": path}}
        except SidecarError as error:
            status, body = error.status, {"ok": False, "error": {"code": error.code, "message": error.message}}
        except PowerContextError as error:
            status = 503 if error.status == 0 else (502 if error.status >= 500 else error.status)
            code = error.code if error.code.startswith("powercontext_") else f"powercontext_{error.code}"
            body = {
                "ok": False,
                "error": {
                    "code": code,
                    "message": error.message,
                    "powercontext_status": error.status,
                    "powercontext_request_id": error.request_id,
                },
            }
            LOGGER.warning(
                "powercontext failure status=%s code=%s request_id=%s",
                error.status, error.code, error.request_id,
            )
        except Exception as error:  # noqa: BLE001 - never leak a half answer
            LOGGER.exception("unhandled sidecar error")
            status, body = 500, {"ok": False, "error": {"code": "sidecar_internal_error", "message": str(error)}}
        self._send(status, body, started)

    # -- helpers -----------------------------------------------------------
    def _session_id(self) -> str:
        session_id = (self.headers.get("X-Session-Id") or "").strip()
        if not session_id:
            raise SidecarError(400, "missing_session", "X-Session-Id header is required")
        if not SESSION_PATTERN.match(session_id):
            raise SidecarError(400, "invalid_session", "X-Session-Id must be 1-256 URL-safe characters")
        return session_id

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as error:
            raise SidecarError(400, "invalid_content_length", "Content-Length must be an integer") from error
        if length > MAX_BODY_BYTES:
            raise SidecarError(413, "body_too_large", f"body exceeds {MAX_BODY_BYTES} bytes")
        return self.rfile.read(length) if length else b""

    def _send(self, status: int, payload: dict[str, Any], started: float) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Sidecar-Request-Id", _request_id())
        self.end_headers()
        self.wfile.write(raw)
        LOGGER.info(
            "%s %s -> %s %.1fms session=%s",
            self.command,
            self.path.split("?", 1)[0],
            status,
            (time.monotonic() - started) * 1000,
            _short(self.headers.get("X-Session-Id") or "-"),
        )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        LOGGER.debug("%s - %s", self.address_string(), format % args)


def _clean_text(text: str) -> str:
    # Keep the user's original wording; only drop NULs and surrounding whitespace.
    return str(text).replace("\x00", "").strip()


def _short(value: str, limit: int = 18) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _request_id() -> str:
    return hashlib.sha256(f"{time.time_ns()}".encode()).hexdigest()[:12]


def build_server(args: argparse.Namespace) -> ThreadingHTTPServer:
    sidecar = Sidecar(
        args.powercontext_url,
        scope_prefix=args.scope_prefix,
        memory_kind=args.memory_kind,
        timeout=args.timeout,
    )
    Handler.sidecar = sidecar
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    return server


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PowerContext sidecar for the Dify tavern NPC")
    parser.add_argument("--host", default=os.environ.get("SIDECAR_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SIDECAR_PORT", "8787")))
    parser.add_argument(
        "--powercontext-url",
        default=os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:18080"),
    )
    parser.add_argument("--scope-prefix", default=os.environ.get("SCOPE_PREFIX", "dify-tavern"))
    parser.add_argument("--memory-kind", default=os.environ.get("MEMORY_KIND", "prank"))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("POWERCONTEXT_TIMEOUT", "15")))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    args = parse_args(argv)
    server = build_server(args)
    LOGGER.info(
        "sidecar listening on http://%s:%s -> PowerContext %s",
        args.host, args.port, args.powercontext_url,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("sidecar stopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
