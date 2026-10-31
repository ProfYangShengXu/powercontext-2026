from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import HOST, PORT
from .db import get_collection
from .ingest import router as ingest_router
from .search import router as search_router

WEB_DIR = Path(__file__).resolve().parent / "web"


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_collection()
    yield


app = FastAPI(
    title="拾遗 · 学习资料库",
    description="本地学习资料导入与混合检索（M2：Web UI）",
    version="0.2.0",
    lifespan=lifespan,
)
app.include_router(ingest_router)
app.include_router(search_router)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
