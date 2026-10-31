#!/usr/bin/env python3
"""最小 seekdb 冒烟：建集合 -> 写入 -> 混合检索 -> 打印。

两种模式：
  * server（Windows 唯一可行，评审同款）：
        HOME=$PWD/.home .venv/bin/python tools/seekdb_smoke.py --host 127.0.0.1 --port 2881
  * embedded（仅 Linux/macOS，且数据目录在 tmpfs/ext4）：
        HOME=$PWD/.home .venv/bin/python tools/seekdb_smoke.py --path /dev/shm/coursecite-smoke

本脚本只依赖 pyseekdb，不依赖本项目其余代码；失败时退出码 != 0。
注意：WSL 下 embedded 的 db 目录必须支持 fallocate，放在 /mnt/d（v9fs）会以
"Fail to fallocate block file" 退出。Windows 请一律用 --host。
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("SEEKDB_HOST") or None,
                    help="seekdb server 地址；不填则走 embedded")
    ap.add_argument("--port", type=int, default=int(os.environ.get("SEEKDB_PORT", "2881")))
    ap.add_argument("--user", default=os.environ.get("SEEKDB_USER") or "root")
    ap.add_argument("--password", default=os.environ.get("SEEKDB_PASSWORD", ""))
    ap.add_argument("--database", default=os.environ.get("SEEKDB_DATABASE", "test"))
    ap.add_argument("--path", default=os.environ.get("SEEKDB_PATH", "/dev/shm/coursecite-smoke"))
    args = ap.parse_args()

    try:
        import pyseekdb
    except ImportError as exc:
        print(f"缺少 pyseekdb：{exc}\n请先 `pip install -e '.[live]'`", file=sys.stderr)
        return 2

    try:
        if args.host:
            client = pyseekdb.Client(
                host=args.host,
                port=args.port,
                user=args.user,
                password=args.password,
                database=args.database,
            )
            target = f"server {args.host}:{args.port}/{args.database}"
        else:
            if sys.platform.startswith("win"):
                print("Windows 不支持 embedded：请用 --host 127.0.0.1 --port 2881", file=sys.stderr)
                return 2
            client = pyseekdb.Client(path=args.path)
            target = f"embedded {args.path}"
        col = client.get_or_create_collection("cc_smoke")
        col.upsert(
            ids=["a", "b", "c"],
            documents=[
                "HNSW 是一种分层可导航小世界图，用于近似最近邻检索",
                "今天天气很好，适合去操场散步",
                "向量检索常用余弦距离衡量相似度",
            ],
            metadatas=[
                {"course": "CSC3170", "lecture": 3, "section": "2.1", "page": 12},
                {"course": "CSC3170", "lecture": 1, "section": "1.0", "page": 1},
                {"course": "CSC3170", "lecture": 5, "section": "5.2", "page": 40},
            ],
        )
        res = col.hybrid_search(
            query={"where_document": {"$contains": "索引"}},
            knn={"query_texts": ["什么结构用于近似最近邻检索"], "n_results": 5},
            rank={"rrf": {"rank_window_size": 60, "rank_constant": 60}},
            n_results=3,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:  # noqa: BLE001 - 冒烟脚本要原样打印失败
        print(f"seekdb 冒烟失败：{exc!r}", file=sys.stderr)
        return 1

    print(json.dumps({k: v for k, v in res.items() if k != "embeddings"}, ensure_ascii=False, indent=2))
    print(f"count={col.count()} target={target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
