from __future__ import annotations

import threading
from typing import Any

import pyseekdb

from .config import (
    COLLECTION_NAME,
    DATA_DIR,
    EMBEDDING_MODEL_DIR,
    REQUIRED_MODEL_FILES,
)

_init_lock = threading.Lock()
_client: Any = None
_collection: Any = None


def _require_embedding_model() -> None:
    missing = [
        name for name in REQUIRED_MODEL_FILES if not (EMBEDDING_MODEL_DIR / name).is_file()
    ]
    if missing:
        raise RuntimeError(
            "seekdb 嵌入模型不完整，缺少文件："
            + ", ".join(missing)
            + f"。请先运行 bash scripts/seed_model.sh（目标目录：{EMBEDDING_MODEL_DIR}）"
        )


def get_collection() -> Any:
    global _client, _collection
    if _collection is not None:
        return _collection

    with _init_lock:
        if _collection is not None:
            return _collection

        _require_embedding_model()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        client = pyseekdb.Client(path=str(DATA_DIR))
        collection = client.get_or_create_collection(name=COLLECTION_NAME)
        _client = client
        _collection = collection
        return collection
