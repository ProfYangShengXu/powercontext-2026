from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from .config import (
    LOG_DIR,
    POWERCONTEXT_BASE_URL,
    POWERCONTEXT_SCOPE_KEY,
    POWERCONTEXT_TIMEOUT_SECONDS,
)
from .db import get_collection

router = APIRouter()

_log_lock = threading.Lock()
_scope_lock = threading.Lock()
_scope_id: str | None = None

_REVIEW_QUERY = "薄弱点 复习"
_PREPARED_BEGIN = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
_MAX_MEMORY_BYTES = 7800


class PowerContextError(RuntimeError):
    pass


class FeedbackRequest(BaseModel):
    doc_id: str = Field(min_length=1, max_length=256)
    verdict: Literal["weak", "mastered"]
    note: str = Field(default="", max_length=1000)
    query: str = Field(default="", max_length=500)


def _append_log(entry: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"memory_{datetime.now().astimezone():%Y%m%d}.jsonl"
    line = json.dumps(entry, ensure_ascii=False, default=str)
    with _log_lock:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _error_text(payload: Any) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail
        if detail is not None:
            return json.dumps(detail, ensure_ascii=False)[:500]
    if isinstance(payload, str) and payload.strip():
        return payload[:500]
    return "无错误详情"


def _request(
    method: str,
    path: str,
    payload: dict[str, Any],
    *,
    operation: str,
    log: bool = True,
) -> dict[str, Any]:
    url = f"{POWERCONTEXT_BASE_URL}{path}"
    started = time.perf_counter()
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    log_base = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "operation": operation,
        "scope_id": _scope_id,
        "request": {"method": method, "path": path, "body": payload},
    }

    try:
        with urllib.request.urlopen(request, timeout=POWERCONTEXT_TIMEOUT_SECONDS) as response:
            http_status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        http_status = exc.code
        raw = exc.read()
        try:
            error_payload: Any = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            error_payload = raw.decode("utf-8", errors="replace")[:1000]
        error = f"PowerContext 返回 HTTP {http_status}（{path}）：{_error_text(error_payload)}"
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        if log:
            _append_log(
                {
                    **log_base,
                    "response": error_payload,
                    "http_status": http_status,
                    "elapsed_ms": elapsed_ms,
                    "ok": False,
                    "error": error,
                }
            )
        raise PowerContextError(error) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        error = f"无法连接 PowerContext（{POWERCONTEXT_BASE_URL}）：{reason}"
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        if log:
            _append_log(
                {
                    **log_base,
                    "response": None,
                    "http_status": None,
                    "elapsed_ms": elapsed_ms,
                    "ok": False,
                    "error": error,
                }
            )
        raise PowerContextError(error) from exc

    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        error = f"PowerContext 返回了非 JSON 响应（{path}）"
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        if log:
            _append_log(
                {
                    **log_base,
                    "response": raw.decode("utf-8", errors="replace")[:1000],
                    "http_status": http_status,
                    "elapsed_ms": elapsed_ms,
                    "ok": False,
                    "error": error,
                }
            )
        raise PowerContextError(error) from exc

    if not isinstance(data, dict):
        error = f"PowerContext 响应不是 JSON 对象（{path}）"
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        if log:
            _append_log(
                {
                    **log_base,
                    "response": data,
                    "http_status": http_status,
                    "elapsed_ms": elapsed_ms,
                    "ok": False,
                    "error": error,
                }
            )
        raise PowerContextError(error)

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    if log:
        _append_log(
            {
                **log_base,
                "response": data,
                "http_status": http_status,
                "elapsed_ms": elapsed_ms,
                "ok": True,
                "error": None,
            }
        )
    return data


def _ensure_scope() -> str:
    global _scope_id
    if _scope_id:
        return _scope_id

    with _scope_lock:
        if _scope_id:
            return _scope_id
        data = _request(
            "POST",
            "/v1/scopes",
            {
                "title": "拾遗 · 学习复习",
                "summary": "本地学习资料库的提问历史、薄弱点与复习状态",
                "idempotency_key": POWERCONTEXT_SCOPE_KEY,
            },
            operation="scopes.create",
            log=False,
        )
        scope_id = data.get("scope_id")
        if not isinstance(scope_id, str) or not scope_id.strip():
            raise PowerContextError("PowerContext 创建 scope 后没有返回 scope_id")
        _scope_id = scope_id.strip()
        print(
            f"[PowerContext] scope_id={_scope_id} "
            f"(idempotency_key={POWERCONTEXT_SCOPE_KEY})",
            flush=True,
        )
        return _scope_id


def bootstrap_powercontext() -> None:
    try:
        _ensure_scope()
    except PowerContextError as exc:
        print(f"[PowerContext] 启动时不可用：{exc}", flush=True)


def _location(metadata: dict[str, Any]) -> str:
    parts = []
    page = metadata.get("page")
    if page is not None:
        parts.append(f"第 {page} 页")
    chunk_index = metadata.get("chunk_index")
    if chunk_index is not None:
        parts.append(f"第 {int(chunk_index) + 1} 块")
    return " · ".join(parts) if parts else "未标注位置"


def _clip(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit].strip()


def _clip_bytes(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore").rstrip() + "…"


def _load_document(doc_id: str) -> dict[str, Any] | None:
    try:
        collection = get_collection()
        result = collection.get(ids=[doc_id], include=["documents", "metadatas"])
    except Exception as exc:
        print(f"[feedback] 读取 doc_id={doc_id} 的原文失败：{type(exc).__name__}: {exc}", flush=True)
        return None

    ids = result.get("ids") or []
    if not ids:
        return None
    documents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    return {
        "text": (documents[0] if documents else "") or "",
        "metadata": (metadatas[0] if metadatas else {}) or {},
    }


def _build_source_request(
    scope_id: str, feedback: FeedbackRequest, document: dict[str, Any] | None
) -> dict[str, Any]:
    metadata = document["metadata"] if document else {}
    source_file = str(metadata.get("source_file") or "")
    location = _location(metadata) if metadata else "未找到对应原文"
    excerpt = _clip(document["text"], 800) if document else ""

    content = "\n".join(
        [
            "【拾遗学习反馈】",
            f"标记: {feedback.verdict}",
            f"doc_id: {feedback.doc_id}",
            f"提问上下文: {feedback.query or '（未提供）'}",
            f"来源文件: {source_file or '（seekdb 中未找到该 doc_id）'}",
            f"位置: {location}",
            f"备注: {feedback.note or '（无）'}",
            "原文摘录:",
            excerpt or "（无原文）",
        ]
    )
    return {
        "scope_id": scope_id,
        "source_id": f"studydesk-feedback-{uuid.uuid4().hex}",
        "content": content,
        "metadata": {
            "app": "studydesk",
            "doc_id": feedback.doc_id,
            "verdict": feedback.verdict,
            "note": feedback.note,
            "question": feedback.query,
            "source_file": source_file,
            "location": location,
            "course": str(metadata.get("course") or ""),
        },
    }


def _build_memory_request(
    scope_id: str, feedback: FeedbackRequest, document: dict[str, Any] | None
) -> dict[str, Any]:
    metadata = document["metadata"] if document else {}
    source_file = str(metadata.get("source_file") or "")
    note = _clip(feedback.note, 300)
    query = _clip(feedback.query, 160)
    excerpt = _clip(document["text"], 260) if document else ""
    anchor = note or query or "相关内容"
    subject = f"《{source_file}》" if source_file else f"文档 {feedback.doc_id}"
    context_phrase = f"提问「{query}」后" if query else "学习反馈中"

    text = (
        f"薄弱点复习：{context_phrase}，用户把{subject}中的「{_clip(anchor, 120)}」标记为未掌握。"
        f"备注：{note or '暂无'}。"
        f"原文线索：{excerpt or '未在 seekdb 中找到原文，按备注记录'}。"
        "复习时优先回看这部分资料。"
    )
    reason = _clip(f"用户标记不懂：{_clip(anchor, 160)}；备注：{note or '无'}", 480)
    return {
        "scope_id": scope_id,
        "kind": "fact",
        "text": _clip_bytes(text, _MAX_MEMORY_BYTES),
        "reason": reason,
    }


def _artifact_ref(artifact: dict[str, Any] | None) -> str | None:
    if not artifact:
        return None
    family = artifact.get("family")
    artifact_id = artifact.get("artifact_id")
    revision = artifact.get("revision")
    if not family or not artifact_id:
        return None
    suffix = f"@{revision}" if revision is not None else ""
    return f"{family}/{artifact_id}{suffix}"


def _submit_feedback(feedback: FeedbackRequest) -> dict[str, Any]:
    scope_id = _ensure_scope()
    document = _load_document(feedback.doc_id)

    source = _request(
        "POST",
        "/v1/sources/content",
        _build_source_request(scope_id, feedback, document),
        operation="sources.content",
    )

    artifact = None
    if feedback.verdict == "weak":
        memory = _request(
            "POST",
            "/v1/memory/remember",
            _build_memory_request(scope_id, feedback, document),
            operation="memory.remember",
        )
        records = memory.get("records") or []
        candidate = records[0].get("artifact") if records and isinstance(records[0], dict) else None
        if not isinstance(candidate, dict):
            raise PowerContextError("PowerContext 写入了 Memory，但响应缺少 artifact 地址")
        artifact = candidate

    return {
        "ok": True,
        "doc_id": feedback.doc_id,
        "verdict": feedback.verdict,
        "scope_id": scope_id,
        "memory_written": feedback.verdict == "weak",
        "source": source.get("source"),
        "source_status": source.get("status"),
        "artifact": artifact,
        "artifact_ref": _artifact_ref(artifact),
    }


def _parse_suggestions(content: str) -> list[dict[str, Any]]:
    marker_at = content.find(_PREPARED_BEGIN)
    if marker_at < 0:
        return []

    fragment = content[marker_at + len(_PREPARED_BEGIN) :].lstrip()
    if not fragment.startswith("{"):
        brace = fragment.find("{")
        if brace < 0:
            return []
        fragment = fragment[brace:]

    try:
        prepared, _ = json.JSONDecoder().raw_decode(fragment)
    except json.JSONDecodeError:
        return []

    if not isinstance(prepared, dict):
        return []

    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in prepared.get("items") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("content") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        suggestion: dict[str, Any] = {
            "text": text,
            "kind": str(item.get("kind") or ""),
            "truncated": bool(item.get("truncated")),
        }
        citation = item.get("citation")
        artifact = citation.get("artifact_ref") if isinstance(citation, dict) else None
        if isinstance(artifact, dict):
            suggestion["artifact"] = artifact
        suggestions.append(suggestion)
    return suggestions


def _build_review() -> dict[str, Any]:
    scope_id = _ensure_scope()
    prepared = _request(
        "POST",
        "/v1/context/prepare",
        {
            "scope_id": scope_id,
            "query": _REVIEW_QUERY,
            "max_bytes": 4000,
        },
        operation="context.prepare",
    )
    content = prepared.get("content") or ""
    return {
        "status": str(prepared.get("status") or "unknown"),
        "suggestions": _parse_suggestions(content),
        "raw_content": content,
    }


def _http_error(exc: PowerContextError) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=(
            f"{exc}。请先启动 PowerContext：powercontext server run"
            f"（{POWERCONTEXT_BASE_URL}）"
        ),
    )


@router.post("/feedback")
async def feedback(request: Request) -> dict[str, Any]:
    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="请求体必须是 UTF-8 编码的 JSON 对象") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")

    try:
        parsed = FeedbackRequest.model_validate(payload)
    except ValidationError as exc:
        errors = [{"loc": list(item["loc"]), "msg": item["msg"]} for item in exc.errors()]
        raise HTTPException(status_code=422, detail=errors) from exc

    parsed = parsed.model_copy(
        update={
            "doc_id": parsed.doc_id.strip(),
            "note": parsed.note.strip(),
            "query": parsed.query.strip(),
        }
    )
    if not parsed.doc_id:
        raise HTTPException(status_code=400, detail="doc_id 不能为空")

    try:
        return _submit_feedback(parsed)
    except PowerContextError as exc:
        raise _http_error(exc) from exc


@router.get("/review")
def review() -> dict[str, Any]:
    try:
        return _build_review()
    except PowerContextError as exc:
        raise _http_error(exc) from exc
