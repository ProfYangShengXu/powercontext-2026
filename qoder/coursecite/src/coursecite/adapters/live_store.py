"""真查 seekdb 的实现：向量 + 全文 + 结构化过滤在库内一条混合检索语句完成。

排序/融合由 seekdb 的 HYBRID_SEARCH 节点负责，本文件只做参数绑定与结果整形。
连接失败一律抛 StoreUnavailable，附可复制的修复命令；**绝不**降级到内存或本地文件。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Sequence

from ..config import PROJECT_ROOT, Settings
from ..core.models import Chunk, Citation, Filters, Hit, Query, Ref, RetrievalRecord
from ..ports.store import IncompleteCorpus, ReadOnlyStore, StoreError, StoreUnavailable

EMBED_MODEL = "all-MiniLM-L6-v2"
EMBED_HF_ID = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384
LOG_TABLE = "retrieval_log"
BATCH_SIZE = 64

_FIX_HINT = """\
seekdb 连不上 / 初始化失败：{error}

可复制的修复方式（任选其一）：
  1) Docker（推荐，评审机同款）：
       cd qoder/coursecite && docker compose up -d
       export SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2881
  2) 嵌入式（仅 Linux/macOS；数据目录必须在 ext4/tmpfs，不能是 /mnt/d 的 v9fs）：
       export SEEKDB_PATH=/dev/shm/coursecite-db
  3) 只想看页面与快照回放（不连库）：
       export COURSECITE_STORE=snapshot
       coursecite serve
"""


def make_embedding_function(cache_dir: Path | None):
    """构造 ONNX 嵌入器；支持自定义缓存目录（默认 ~/.cache/pyseekdb）。"""
    try:
        from pyseekdb.utils.embedding_functions import OnnxEmbeddingFunction
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出明确指令
        raise StoreUnavailable("缺少 pyseekdb，请先 `pip install -e '.[live]'`") from exc
    kwargs: dict[str, Any] = {
        "model_name": EMBED_MODEL,
        "hf_model_id": EMBED_HF_ID,
        "dimension": EMBED_DIM,
    }
    if cache_dir is not None:
        kwargs["download_path"] = Path(cache_dir).expanduser() / "onnx_models" / EMBED_MODEL
    return OnnxEmbeddingFunction(**kwargs)


class LiveStore:
    mode = "live"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None
        self._collection = None
        self._log_ready = False
        self._sql_trace: list[str] = []
        self._trace_enabled = False
        self._connect()

    # ------------------------------------------------------------------ 连接
    def _connect(self) -> None:
        try:
            import pyseekdb  # noqa: F401
        except ImportError as exc:
            raise StoreUnavailable("缺少 pyseekdb，请先 `pip install -e '.[live]'`") from exc
        try:
            if self._settings.seekdb_host:
                self._client = pyseekdb.Client(
                    host=self._settings.seekdb_host,
                    port=self._settings.seekdb_port,
                    user=self._settings.seekdb_user,
                    password=self._settings.seekdb_password,
                    database=self._settings.seekdb_database,
                )
            else:
                path = self._settings.seekdb_path or str(PROJECT_ROOT / ".seekdb-data")
                self._client = pyseekdb.Client(path=path)
            embedding_function = make_embedding_function(self._settings.embed_cache)
            self._collection = self._client.get_or_create_collection(
                name=self._settings.collection,
                embedding_function=embedding_function,
            )
            # 立刻触发一次真实连接，让失败发生在启动时而不是第一次查询时。
            self._collection.count()
        except StoreUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转成带修复命令的显式失败
            raise StoreUnavailable(_FIX_HINT.format(error=exc)) from exc
        self._install_trace()

    @property
    def _server(self):
        # pyseekdb 的公共 API 不暴露裸 SQL；retrieval_log 与 SQL 追踪统一走这一处。
        return self._client._server  # noqa: SLF001 - 适配点集中在此

    def _install_trace(self) -> None:
        server = self._server
        original = server._execute_query_with_cursor  # noqa: SLF001

        def traced(conn, sql, params, use_context_manager=True):
            if self._trace_enabled:
                self._sql_trace.append(sql)
            return original(conn, sql, params, use_context_manager)

        server._execute_query_with_cursor = traced  # noqa: SLF001

    def _select(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        server = self._server
        conn = server._ensure_connection()  # noqa: SLF001
        use_cm = server._use_context_manager_for_cursor()  # noqa: SLF001
        return server._execute_query_with_cursor(conn, sql, list(params), use_cm)  # noqa: SLF001

    # ------------------------------------------------------------------ 检索
    @staticmethod
    def _where(filters: Filters) -> dict[str, Any]:
        where: dict[str, Any] = {}
        if filters.course:
            where["course"] = {"$eq": filters.course}
        if filters.max_lecture is not None:
            where["lecture"] = {"$lte": int(filters.max_lecture)}
        return where

    @staticmethod
    def _fulltext_terms(text: str) -> str:
        """把查询压成全文臂可用的词串：保留 CJK、字母数字，去掉标点。"""
        import re

        tokens = re.findall(r"[A-Za-z0-9_.\-]+|[\u4e00-\u9fff]+", text)
        return " ".join(tokens) or text

    def _search_parm(self, query: Query) -> tuple[dict, dict, dict]:
        where = self._where(query.filters)
        query_arm: dict[str, Any] = {"where_document": {"$contains": self._fulltext_terms(query.text)}}
        knn_arm: dict[str, Any] = {
            "query_texts": [query.text],
            "n_results": max(query.limit, 10),
        }
        if where:
            query_arm["where"] = where
            knn_arm["where"] = where
        return query_arm, knn_arm, {"rrf": {"rank_window_size": 60, "rank_constant": 60}}

    def search(self, query: Query) -> list[Hit]:
        query_arm, knn_arm, rank = self._search_parm(query)
        self._sql_trace.clear()
        self._trace_enabled = True
        try:
            result = self._collection.hybrid_search(
                query=query_arm,
                knn=knn_arm,
                rank=rank,
                n_results=query.limit,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:  # noqa: BLE001
            raise StoreError(f"混合检索失败：{exc}") from exc
        finally:
            self._trace_enabled = False
        return self._to_hits(result)

    def _to_hits(self, result: dict) -> list[Hit]:
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        hits: list[Hit] = []
        for rank, chunk_id in enumerate(ids, start=1):
            meta = metadatas[rank - 1] if rank - 1 < len(metadatas) else {}
            meta = meta or {}
            score = float(distances[rank - 1]) if rank - 1 < len(distances) else 0.0
            document = documents[rank - 1] if rank - 1 < len(documents) else ""
            hits.append(
                Hit(
                    rank=rank,
                    chunk_id=str(chunk_id),
                    score=score,
                    channels=("hybrid",),
                    snippet=str(meta.get("snippet", document[:180])),
                    citation=self._citation_from_meta(meta),
                    text=document,
                )
            )
        return hits

    @staticmethod
    def _citation_from_meta(meta: dict) -> Citation:
        page = meta.get("page")
        return Citation(
            course=str(meta.get("course", "")),
            course_name=str(meta.get("course_name", meta.get("course", ""))),
            lecture=int(meta.get("lecture", 0)),
            section=str(meta.get("section", "")),
            section_title=str(meta.get("section_title", "")),
            page=None if page is None or int(page) < 0 else int(page),
            path=str(meta.get("path", "")),
            line_start=int(meta.get("line_start", 0)),
        )

    def last_sql(self) -> str | None:
        """最近一次检索里真正发给 seekdb 的混合检索 SQL（供 --show-sql 证据）。"""
        for sql in reversed(self._sql_trace):
            if "from" in sql.lower():
                return sql
        return self._sql_trace[-1] if self._sql_trace else None

    # ------------------------------------------------------------------ 写入
    @staticmethod
    def _metadata(chunk: Chunk) -> dict:
        c = chunk.citation
        return {
            "course": c.course,
            "course_name": c.course_name,
            "lecture": c.lecture,
            "section": c.section,
            "section_title": c.section_title,
            "page": -1 if c.page is None else c.page,
            "path": c.path,
            "line_start": c.line_start,
            "snippet": chunk.snippet,
            "content_hash": chunk.content_hash,
        }

    def put(self, chunks: Sequence[Chunk]) -> int:
        if not chunks:
            raise IncompleteCorpus("没有可写入的段落")
        written = 0
        for start in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[start : start + BATCH_SIZE]
            try:
                self._collection.upsert(
                    ids=[c.id for c in batch],
                    documents=[c.text for c in batch],
                    metadatas=[self._metadata(c) for c in batch],
                )
            except Exception as exc:  # noqa: BLE001
                raise StoreError(f"写入 seekdb 失败（第 {start} 条起）：{exc}") from exc
            written += len(batch)
        return written

    def reset(self) -> None:
        try:
            self._client.delete_collection(self._settings.collection)
            embedding_function = make_embedding_function(self._settings.embed_cache)
            self._collection = self._client.get_or_create_collection(
                name=self._settings.collection, embedding_function=embedding_function
            )
            self._log_ready = False
        except Exception as exc:  # noqa: BLE001
            raise StoreError(f"重建索引失败：{exc}") from exc

    def obtain(self, ref: Ref) -> Chunk | None:
        try:
            result = self._collection.get(ids=[ref.chunk_id], include=["documents", "metadatas"])
        except Exception as exc:  # noqa: BLE001
            raise StoreError(f"取原文失败：{exc}") from exc
        ids = (result.get("ids") or [])
        if not ids:
            return None
        meta = (result.get("metadatas") or [{}])[0] or {}
        document = (result.get("documents") or [""])[0] or ""
        return Chunk(
            id=str(ids[0]),
            text=document,
            snippet=str(meta.get("snippet", document[:180])),
            citation=self._citation_from_meta(meta),
            content_hash=str(meta.get("content_hash", "")),
        )

    # ------------------------------------------------------------------ 留痕
    def _ensure_log_table(self) -> None:
        if self._log_ready:
            return
        try:
            self._select(
                f"""
                CREATE TABLE IF NOT EXISTS {LOG_TABLE} (
                    id BIGINT NOT NULL AUTO_INCREMENT,
                    created_at VARCHAR(40) NOT NULL,
                    query_text TEXT NOT NULL,
                    filters VARCHAR(255) NOT NULL,
                    top_ids TEXT NOT NULL,
                    elapsed_ms INT NOT NULL,
                    channel VARCHAR(32) NOT NULL,
                    store_mode VARCHAR(16) NOT NULL,
                    PRIMARY KEY (id)
                ) DEFAULT CHARSET=utf8mb4
                """
            )
        except Exception as exc:  # noqa: BLE001
            raise StoreError(f"创建 {LOG_TABLE} 失败：{exc}") from exc
        self._log_ready = True

    def record_search(self, record: RetrievalRecord) -> None:
        self._ensure_log_table()
        try:
            self._select(
                f"INSERT INTO {LOG_TABLE} "
                "(created_at, query_text, filters, top_ids, elapsed_ms, channel, store_mode) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    record.created_at,
                    record.query,
                    json.dumps(record.filters.to_dict(), ensure_ascii=False),
                    json.dumps(list(record.top_ids), ensure_ascii=False),
                    record.elapsed_ms,
                    record.channel,
                    record.store_mode,
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise StoreError(f"写 {LOG_TABLE} 失败：{exc}") from exc

    def history(self, limit: int) -> list[RetrievalRecord]:
        self._ensure_log_table()
        try:
            rows = self._select(
                f"SELECT created_at, query_text, filters, top_ids, elapsed_ms, channel, store_mode "
                f"FROM {LOG_TABLE} ORDER BY id DESC LIMIT %s",
                [int(limit)],
            )
        except Exception as exc:  # noqa: BLE001
            raise StoreError(f"读 {LOG_TABLE} 失败：{exc}") from exc
        records: list[RetrievalRecord] = []
        for row in rows:
            filters = json.loads(row.get("filters") or "{}")
            top_ids = json.loads(row.get("top_ids") or "[]")
            records.append(
                RetrievalRecord(
                    query=str(row.get("query_text", "")),
                    filters=Filters.from_dict(filters),
                    top_ids=tuple(str(x) for x in top_ids),
                    elapsed_ms=int(row.get("elapsed_ms") or 0),
                    channel=str(row.get("channel", "")),
                    store_mode=str(row.get("store_mode", "")),
                    created_at=str(row.get("created_at", "")),
                )
            )
        return records

    def stats(self) -> dict:
        chunks = int(self._collection.count())
        log_rows = 0
        try:
            rows = self._select(f"SELECT COUNT(*) AS n FROM {LOG_TABLE}")
            log_rows = int(rows[0]["n"]) if rows else 0
        except Exception:  # noqa: BLE001 - 表还没建就是 0 行，不是错误
            log_rows = 0
        return {
            "mode": self.mode,
            "collection": self._settings.collection,
            "chunks": chunks,
            "retrieval_log_rows": log_rows,
            "seekdb": self._settings.seekdb_host or self._settings.seekdb_path or str(PROJECT_ROOT / ".seekdb-data"),
        }

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client._cleanup()  # noqa: SLF001
            except Exception:  # noqa: BLE001 - 退出清理失败不影响交付
                pass
