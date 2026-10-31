"""Web UI：只调用 SearchService，不判断当前是线上还是本地。

前端渲染 `hits` 的数组顺序，不做任何排序/融合/加权。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import PROJECT_ROOT, Settings
from .core.service import SearchService
from .ports.store import NoMatch, StoreError

WEB_DIR = PROJECT_ROOT / "web"


def _suggestions(settings: Settings, service: SearchService) -> list[str]:
    available = service.available_queries()
    if available:
        return available
    path = settings.data_dir.parent / "queries.json"
    if path.is_file():
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
            return [str(item["query"]) for item in items if item.get("query")]
        except (json.JSONDecodeError, KeyError, TypeError):
            return []
    return []


def create_app(service: SearchService, settings: Settings) -> FastAPI:
    if not (WEB_DIR / "index.html").is_file():
        raise StoreError(f"前端目录缺少 index.html：{WEB_DIR}")
    app = FastAPI(title="课引 CourseCite", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, **service.stats()}

    @app.get("/api/meta")
    def meta() -> dict:
        return {
            "store_mode": service.store_mode,
            "stats": service.stats(),
            "suggestions": _suggestions(settings, service),
        }

    @app.get("/api/search")
    def search(
        q: str = Query(..., min_length=1),
        course: str | None = None,
        lecture_max: int | None = None,
    ) -> JSONResponse:
        try:
            response = service.search(q, course=course, max_lecture=lecture_max)
        except NoMatch as exc:
            return JSONResponse(
                status_code=404,
                content={"error": str(exc), "available": service.available_queries()},
            )
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        except StoreError as exc:
            return JSONResponse(status_code=503, content={"error": str(exc)})
        return JSONResponse(content=response.to_dict(include_text=False))

    @app.get("/api/obtain/{chunk_id}")
    def obtain(chunk_id: str) -> JSONResponse:
        try:
            chunk = service.obtain(chunk_id)
        except StoreError as exc:
            return JSONResponse(status_code=503, content={"error": str(exc)})
        if chunk is None:
            return JSONResponse(status_code=404, content={"error": f"找不到段落 {chunk_id}"})
        return JSONResponse(content=chunk.to_dict())

    @app.get("/api/log")
    def log(limit: int = 5) -> JSONResponse:
        try:
            records = service.history(max(1, min(limit, 50)))
        except StoreError as exc:
            return JSONResponse(status_code=503, content={"error": str(exc)})
        return JSONResponse(content=[record.to_dict() for record in records])

    return app
