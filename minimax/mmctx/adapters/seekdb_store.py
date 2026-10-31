"""StorePort 的 seekdb 实现：嵌入式 seekdb + 原生全文/向量索引 + 单条混合候选 SQL。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Sequence

import pymysql

from mmctx.adapters.embedding import LocalHashedEmbedding
from mmctx.ports.store import Chunk, Doc, Hit, SearchQuery, StoreStats

TABLE = "mm_doc_chunks"
META = "mm_meta"
VECTOR_INDEX = "ix_mm_vec"
FTS_INDEX = "ix_mm_fts"

# 两条臂各自在派生表里 LIMIT，全文与向量索引才能各取 top-k；
# 单条 UNION ALL 语句，融合在应用层按 RRF 做。
_CANDIDATE_SQL = f"""
SELECT chunk_uid, doc_id, doc_kind, path, start_line, end_line, content_hash, content, fts_score, vec_dist, arm
FROM (
  SELECT chunk_uid, doc_id, doc_kind, path, start_line, end_line, content_hash, content,
         MATCH(searchable) AGAINST (%(q)s IN NATURAL LANGUAGE MODE) AS fts_score,
         NULL AS vec_dist, 'fts' AS arm
  FROM {TABLE}
  WHERE is_current = 1 AND MATCH(searchable) AGAINST (%(q)s IN NATURAL LANGUAGE MODE)
  ORDER BY fts_score DESC
  LIMIT %(k)s
) AS fts_arm
UNION ALL
SELECT chunk_uid, doc_id, doc_kind, path, start_line, end_line, content_hash, content, fts_score, vec_dist, arm
FROM (
  SELECT chunk_uid, doc_id, doc_kind, path, start_line, end_line, content_hash, content,
         NULL AS fts_score,
         l2_distance(embedding, %(v)s) AS vec_dist, 'vector' AS arm
  FROM {TABLE}
  WHERE is_current = 1
  ORDER BY l2_distance(embedding, %(v)s) APPROXIMATE
  LIMIT %(k)s
) AS vec_arm
"""


class StoreUnavailableError(RuntimeError):
    pass


class SeekdbStore:
    """嵌入式 seekdb 的 StorePort 实现。

    - 连接信息来自 pylibseekdb 的实例，本类不读环境变量。
    - 内容 hash 决定块身份：改内容 = 新块，旧块 is_current=0。
    - 检索恰好一条语句，同时取 FTS 与向量候选。
    """

    def __init__(self, path: Path, *, embedding_dim: int = 384) -> None:
        self.path = Path(path)
        self.embedding_dim = embedding_dim
        self.embedding = LocalHashedEmbedding(embedding_dim)
        self._instance: Any = None
        self._conn: Any = None
        self.parser = ""
        self.statements_in_last_search = 0

    # -- 生命周期 ---------------------------------------------------------
    def connect(self) -> "SeekdbStore":
        if self._conn is not None:
            return self
        try:
            import pylibseekdb
        except ModuleNotFoundError as error:
            raise StoreUnavailableError(
                "缺少 pylibseekdb：请先执行 make init（或 pip install pylibseekdb）"
            ) from error
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            self._instance = pylibseekdb.open(db_dir=str(self.path))
        except Exception as error:  # noqa: BLE001 - 统一成可读错误
            raise StoreUnavailableError(
                f"无法打开 seekdb 数据目录 {self.path}：{error}。"
                "数据目录必须位于本地 Linux 文件系统（WSL 下用 /mnt/wsl，不要用 /mnt/d）"
            ) from error
        options = dict(self._instance.connection_options())
        self._conn = pymysql.connect(
            **options,
            database="test",
            charset="utf8mb4",
            autocommit=False,
            init_command="SET autocommit = 0",
        )
        # seekdb 默认 ob_query_timeout 只有 10s：冷启动建 HNSW 索引或首次
        # executemany 写入 46 条向量就可能超时（实测 4012）。放宽到 60s。
        with self._conn.cursor() as cursor:
            cursor.execute("SET ob_query_timeout = 60000000")
        self.init_schema()
        return self

    def init_schema(self) -> None:
        # 快路径：表已建好时不再发任何 DDL。seekdb 的 CREATE TABLE IF NOT EXISTS
        # 仍会拿 DDL 锁并等内部 schema 初始化，是冷启动耗时的大头。
        if self._schema_ready():
            return
        with self._conn.cursor() as cursor:
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS {META} (k VARCHAR(64) NOT NULL PRIMARY KEY, "
                "v VARCHAR(256) NOT NULL) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"
            )
            cursor.execute(
                f"""CREATE TABLE IF NOT EXISTS {TABLE} (
                    chunk_uid    CHAR(32)  NOT NULL,
                    doc_id       CHAR(16)  NOT NULL,
                    doc_kind     VARCHAR(16) NOT NULL,
                    path         VARCHAR(512) NOT NULL,
                    start_line   BIGINT NOT NULL,
                    end_line     BIGINT NOT NULL,
                    content      LONGTEXT NOT NULL,
                    searchable   LONGTEXT NOT NULL,
                    content_hash CHAR(16) NOT NULL,
                    is_current   TINYINT NOT NULL DEFAULT 1,
                    embedding    VECTOR({self.embedding_dim}) NOT NULL,
                    PRIMARY KEY (chunk_uid),
                    KEY ix_mm_doc (doc_id, is_current),
                    KEY ix_mm_kind (doc_kind, is_current),
                    VECTOR INDEX {VECTOR_INDEX} (embedding) WITH (distance=l2, type=hnsw, lib=vsag)
                ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin ORGANIZATION = HEAP"""
            )
            self._conn.commit()
            parser = self._meta_get("fts_parser")
            if parser is None or not self._index_exists("fts"):
                parser = self._create_fulltext_index()
                self._meta_set("fts_parser", parser)
                self._conn.commit()
            self.parser = parser

    def _schema_ready(self) -> bool:
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()"
                )
                tables = {str(row[0]) for row in cursor.fetchall()}
                if not {TABLE, META} <= tables:
                    return False
                parser = self._meta_get("fts_parser")
                if not parser:
                    return False
                self.parser = parser
                return self._index_exists("fts") and self._index_exists("vec")
        except pymysql.MySQLError:
            return False

    def _index_exists(self, which: str) -> bool:
        name = FTS_INDEX if which == "fts" else VECTOR_INDEX
        with self._conn.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s",
                (TABLE, name),
            )
            return int(cursor.fetchone()[0]) > 0

    def _create_fulltext_index(self) -> str:
        # 中文必须用 IK / NGRAM；SPACE 会把连续汉字当成一个 token。按可用性降级，
        # 并把实际使用的 parser 记进 meta，证据可复查。
        for parser in ("IK", "NGRAM", "SPACE"):
            with self._conn.cursor() as cursor:
                try:
                    cursor.execute(
                        f"CREATE FULLTEXT INDEX {FTS_INDEX} ON {TABLE} (searchable) WITH PARSER {parser}"
                    )
                    self._conn.commit()
                    return parser
                except pymysql.MySQLError as error:
                    if error.args[0] == 1061:  # 并发/重复创建
                        self._conn.commit()
                        return parser
                    if error.args[0] in (1128, 1105):  # 未定义的 parser / 不支持
                        continue
                    raise
        raise StoreUnavailableError("seekdb 无法创建任何全文索引 parser（IK/NGRAM/SPACE）")

    def _meta_get(self, key: str) -> str | None:
        with self._conn.cursor() as cursor:
            cursor.execute(f"SELECT v FROM {META} WHERE k = %s", (key,))
            row = cursor.fetchone()
            return str(row[0]) if row else None

    def _meta_set(self, key: str, value: str) -> None:
        with self._conn.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {META} (k, v) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE v = VALUES(v)",
                (key, value),
            )

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
        if self._instance is not None:
            try:
                self._instance.close()
            finally:
                self._instance = None

    def __enter__(self) -> "SeekdbStore":
        return self.connect()

    def __exit__(self, *_: object) -> None:
        self.close()

    def reset(self) -> None:
        with self._conn.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {TABLE}")
            cursor.execute(f"DROP TABLE IF EXISTS {META}")
        self._conn.commit()
        self.parser = ""
        self.init_schema()

    # -- 写入 -------------------------------------------------------------
    def upsert_document(self, doc: Doc) -> dict[str, int]:
        rows = []
        for chunk in doc.chunks:
            searchable = f"{chunk.path} {chunk.symbol} {chunk.content}".strip()
            vector = self.embedding.encode_one(searchable)
            rows.append(
                (
                    chunk.chunk_uid,
                    chunk.doc_id,
                    chunk.kind,
                    chunk.path,
                    chunk.start_line,
                    chunk.end_line,
                    chunk.content,
                    searchable,
                    chunk.content_hash,
                    1,
                    LocalHashedEmbedding.as_literal(vector),
                )
            )
        uids = [chunk.chunk_uid for chunk in doc.chunks]
        inserted = 0
        invalidated = 0
        try:
            with self._conn.cursor() as cursor:
                if rows:
                    before = self._count_doc(doc.doc_id)
                    cursor.executemany(
                        f"INSERT INTO {TABLE} (chunk_uid, doc_id, doc_kind, path, start_line, end_line, "
                        "content, searchable, content_hash, is_current, embedding) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON DUPLICATE KEY UPDATE is_current = 1, content = VALUES(content), "
                        "searchable = VALUES(searchable), path = VALUES(path)",
                        rows,
                    )
                    inserted = len(rows)
                    placeholders = ",".join(["%s"] * len(uids)) if uids else "NULL"
                    cursor.execute(
                        f"UPDATE {TABLE} SET is_current = 0 WHERE doc_id = %s AND chunk_uid NOT IN ({placeholders})",
                        (doc.doc_id, *uids),
                    )
                    invalidated = max(0, before - len(uids))
                else:
                    cursor.execute(f"UPDATE {TABLE} SET is_current = 0 WHERE doc_id = %s", (doc.doc_id,))
            self._conn.commit()
        except pymysql.MySQLError:
            self._conn.rollback()
            raise
        return {"inserted": inserted, "invalidated": invalidated}

    def _count_doc(self, doc_id: str) -> int:
        with self._conn.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE doc_id = %s AND is_current = 1", (doc_id,))
            return int(cursor.fetchone()[0])

    # -- 检索 -------------------------------------------------------------
    def _query_params(self, query: SearchQuery) -> dict[str, object]:
        vector = self.embedding.encode_one(query.text)
        return {"q": query.text, "v": LocalHashedEmbedding.as_literal(vector), "k": int(query.limit)}

    def search(self, query: SearchQuery) -> list[Hit]:
        params = self._query_params(query)
        self.statements_in_last_search = 0
        with self._conn.cursor() as cursor:
            cursor.execute(_CANDIDATE_SQL, params)
            self.statements_in_last_search += 1
            rows = cursor.fetchall()
        return [
            Hit(
                chunk_uid=row[0],
                doc_id=row[1],
                kind=row[2],
                path=row[3],
                start_line=int(row[4]),
                end_line=int(row[5]),
                content_hash=row[6],
                content=row[7],
                fts_score=None if row[8] is None else float(row[8]),
                vec_dist=None if row[9] is None else float(row[9]),
                arm=row[10],
            )
            for row in rows
        ]

    def lookup(self, chunk_uid: str) -> Hit | None:
        with self._conn.cursor() as cursor:
            cursor.execute(
                f"SELECT chunk_uid, doc_id, doc_kind, path, start_line, end_line, content_hash, content "
                f"FROM {TABLE} WHERE chunk_uid = %s AND is_current = 1",
                (chunk_uid,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return Hit(
            chunk_uid=row[0],
            doc_id=row[1],
            kind=row[2],
            path=row[3],
            start_line=int(row[4]),
            end_line=int(row[5]),
            content_hash=row[6],
            content=row[7],
        )

    def stats(self) -> StoreStats:
        with self._conn.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {TABLE}")
            total = int(cursor.fetchone()[0])
            cursor.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE is_current = 1")
            current = int(cursor.fetchone()[0])
            cursor.execute(f"SELECT COUNT(DISTINCT doc_id) FROM {TABLE} WHERE is_current = 1")
            docs = int(cursor.fetchone()[0])
        return StoreStats(
            chunks=total, docs=docs, current_chunks=current, table=TABLE, parser=self.parser or "unknown"
        )

    def explain(self, query: SearchQuery) -> list[str]:
        params = self._query_params(query)
        lines: list[str] = []
        with self._conn.cursor() as cursor:
            cursor.execute("EXPLAIN " + _CANDIDATE_SQL, params)
            for row in cursor.fetchall():
                for cell in row:
                    if cell is not None:
                        lines.append(str(cell))
        return lines

    def warm_vectors(self, timeout_s: float = 15.0) -> dict[str, int]:
        """HNSW 索引是增量构建的：刚写入的块可能还查不到。

        用一个 ANN 计数查询把它推到稳定（新进程也能立即查全），返回期望/实际条数。
        这是实测行为：插入后第一次 ANN 只回部分行，第二次才全。
        """
        with self._conn.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE is_current = 1")
            expected = int(cursor.fetchone()[0])
        if expected == 0:
            return {"expected": 0, "warm": 0}
        vector = LocalHashedEmbedding.as_literal(self.embedding.encode_one("warm up"))
        deadline = time.monotonic() + timeout_s
        actual = 0
        while time.monotonic() < deadline:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT COUNT(*) FROM (SELECT chunk_uid FROM {TABLE} WHERE is_current = 1 "
                    "ORDER BY l2_distance(embedding, %s) APPROXIMATE LIMIT %s) AS warm",
                    (vector, expected),
                )
                actual = int(cursor.fetchone()[0])
            if actual >= expected:
                break
            time.sleep(0.2)
        return {"expected": expected, "warm": actual}

    def all_current_paths(self) -> list[str]:
        with self._conn.cursor() as cursor:
            cursor.execute(f"SELECT DISTINCT path FROM {TABLE} WHERE is_current = 1 ORDER BY path")
            return [str(row[0]) for row in cursor.fetchall()]

    def chunk_count_by_kind(self) -> dict[str, int]:
        with self._conn.cursor() as cursor:
            cursor.execute(
                f"SELECT doc_kind, COUNT(*) FROM {TABLE} WHERE is_current = 1 GROUP BY doc_kind"
            )
            return {str(row[0]): int(row[1]) for row in cursor.fetchall()}

    def timed_search(self, query: SearchQuery) -> tuple[list[Hit], int]:
        started = time.perf_counter()
        hits = self.search(query)
        return hits, int((time.perf_counter() - started) * 1000)


def chunks_for(doc: Doc) -> Sequence[Chunk]:
    return doc.chunks
