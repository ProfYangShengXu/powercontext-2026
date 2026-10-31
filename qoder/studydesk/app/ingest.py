from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from pypdf import PdfReader

from .config import (
    BASE_DIR,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    INGEST_BATCH_SIZE,
    MAX_COURSE_LENGTH,
    SUPPORTED_SUFFIXES,
)
from .db import get_collection

router = APIRouter()
_ingest_lock = threading.Lock()

_BOUNDARIES = ("\n\n", "\n", "。", "；", "！", "？", ". ", "; ", "，", ", ")


class IngestRequest(BaseModel):
    paths: list[str] = Field(min_length=1)
    course: str = Field(min_length=1, max_length=MAX_COURSE_LENGTH)


@dataclass(frozen=True)
class TextChunk:
    text: str
    char_start: int
    page: int | None
    chunk_index: int


@dataclass(frozen=True)
class SourceFile:
    path: Path
    source_file: str


def _normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _chunk_segment(text: str, page: int | None) -> list[tuple[str, int, int | None]]:
    text = _normalize_text(text)
    length = len(text)
    chunks: list[tuple[str, int, int | None]] = []
    start = 0

    while start < length:
        raw_end = min(start + CHUNK_SIZE, length)
        end = raw_end

        if raw_end < length:
            search_from = start + max(CHUNK_SIZE // 2, CHUNK_OVERLAP + 1)
            boundary = -1
            for separator in _BOUNDARIES:
                found = text.rfind(separator, search_from, raw_end)
                if found >= 0:
                    boundary = max(boundary, found + len(separator))
            if boundary > start:
                end = boundary

        raw_chunk = text[start:end]
        stripped = raw_chunk.strip()
        if stripped:
            leading = len(raw_chunk) - len(raw_chunk.lstrip())
            chunks.append((stripped, start + leading, page))

        if end >= length:
            break
        start = max(start + 1, end - CHUNK_OVERLAP)

    return chunks


def _extract_chunks(path: Path) -> list[TextChunk]:
    suffix = path.suffix.lower()
    segments: list[tuple[str, int | None]] = []

    if suffix == ".pdf":
        reader = PdfReader(str(path))
        for page_number, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text() or ""
            if page_text.strip():
                segments.append((page_text, page_number))
        if not segments:
            raise ValueError("PDF 没有可提取的文本层（扫描件需要 OCR）")
    else:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("文件不是有效的 UTF-8 文本") from exc
        if text.strip():
            segments.append((text, None))
        else:
            raise ValueError("文件内容为空")

    raw_chunks: list[tuple[str, int, int | None]] = []
    for text, page in segments:
        raw_chunks.extend(_chunk_segment(text, page))

    return [
        TextChunk(text=text, char_start=char_start, page=page, chunk_index=index)
        for index, (text, char_start, page) in enumerate(raw_chunks)
    ]


def _doc_id(course: str, source_file: str, chunk: TextChunk) -> str:
    key = f"{course}\0{source_file}\0{chunk.chunk_index}\0{chunk.text}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def _is_timeout_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(token in message for token in ("4012", "timeout", "timed out", "deadline"))


def _add_batch(collection: object, ids: list[str], documents: list[str], metadatas: list[dict]) -> None:
    try:
        collection.add(ids=ids, documents=documents, metadatas=metadatas)
        return
    except Exception as exc:
        if not _is_timeout_error(exc):
            raise
        time.sleep(0.5)
        collection.add(ids=ids, documents=documents, metadatas=metadatas)


def _write_chunks(course: str, source_file: str, chunks: list[TextChunk]) -> int:
    collection = get_collection()
    imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    records = []
    for chunk in chunks:
        metadata = {
            "course": course,
            "source_file": source_file,
            "chunk_index": chunk.chunk_index,
            "char_start": chunk.char_start,
            "file_type": Path(source_file).suffix.lower().lstrip("."),
            "imported_at": imported_at,
        }
        if chunk.page is not None:
            metadata["page"] = chunk.page
        records.append(
            {
                "id": _doc_id(course, source_file, chunk),
                "document": chunk.text,
                "metadata": metadata,
            }
        )

    with _ingest_lock:
        collection.delete(
            where={
                "$and": [
                    {"course": {"$eq": course}},
                    {"source_file": {"$eq": source_file}},
                ]
            }
        )
        for offset in range(0, len(records), INGEST_BATCH_SIZE):
            batch = records[offset : offset + INGEST_BATCH_SIZE]
            try:
                _add_batch(
                    collection,
                    ids=[record["id"] for record in batch],
                    documents=[record["document"] for record in batch],
                    metadatas=[record["metadata"] for record in batch],
                )
            except Exception as exc:
                batch_number = offset // INGEST_BATCH_SIZE + 1
                raise RuntimeError(
                    f"{source_file} 第 {batch_number} 批写入失败：{type(exc).__name__}: {exc}"
                ) from exc

    return len(records)


def _collect_files(raw_paths: list[str]) -> tuple[list[SourceFile], list[dict]]:
    files: dict[Path, SourceFile] = {}
    errors: list[dict] = []

    for raw_path in raw_paths:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = BASE_DIR / path
        try:
            path = path.resolve(strict=True)
        except (OSError, RuntimeError):
            errors.append(
                {
                    "source_file": raw_path,
                    "chunks": 0,
                    "status": "error",
                    "error": "路径不存在或不可访问",
                }
            )
            continue

        if path.is_dir():
            candidates = sorted(
                child
                for child in path.rglob("*")
                if child.is_file() and child.suffix.lower() in SUPPORTED_SUFFIXES
            )
            if not candidates:
                errors.append(
                    {
                        "source_file": raw_path,
                        "chunks": 0,
                        "status": "error",
                        "error": "目录中没有支持的 .md/.txt/.pdf 文件",
                    }
                )
                continue
            for child in candidates:
                resolved = child.resolve()
                files.setdefault(
                    resolved,
                    SourceFile(path=resolved, source_file=child.relative_to(path).as_posix()),
                )
        elif path.is_file():
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                errors.append(
                    {
                        "source_file": raw_path,
                        "chunks": 0,
                        "status": "error",
                        "error": "仅支持 .md/.txt/.pdf",
                    }
                )
                continue
            files.setdefault(path, SourceFile(path=path, source_file=path.name))
        else:
            errors.append(
                {
                    "source_file": raw_path,
                    "chunks": 0,
                    "status": "error",
                    "error": "不是普通文件或目录",
                }
            )

    return list(files.values()), errors


@router.post("/ingest")
def ingest(request: IngestRequest) -> dict:
    course = request.course.strip()
    if not course:
        raise HTTPException(status_code=400, detail="course 不能为空")

    raw_paths = [item.strip() for item in request.paths]
    if any(not item for item in raw_paths):
        raise HTTPException(status_code=400, detail="paths 不能包含空字符串")

    try:
        get_collection()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    files, errors = _collect_files(raw_paths)
    results: list[dict] = []
    total_chunks = 0

    for source in files:
        try:
            chunks = _extract_chunks(source.path)
            if not chunks:
                raise ValueError("切块后没有可写入的内容")
            written = _write_chunks(course, source.source_file, chunks)
            total_chunks += written
            results.append(
                {
                    "source_file": source.source_file,
                    "chunks": written,
                    "status": "ok",
                    "error": None,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "source_file": source.source_file,
                    "chunks": 0,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    results.extend(errors)
    return {
        "course": course,
        "files": results,
        "total_chunks": total_chunks,
        "ok": not any(item["status"] == "error" for item in results),
    }
