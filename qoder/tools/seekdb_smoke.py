#!/usr/bin/env python3
"""最小 seekdb 冒烟：建集合 -> 写入 -> 混合检索 -> 打印。

用途（README 第 2 步的快速自检）：
    HOME=$PWD/.home .venv/bin/python tools/seekdb_smoke.py [--path /dev/shm/coursecite-db]

本脚本只依赖 pyseekdb，不依赖本项目其余代码；失败时退出码 != 0。
注意：WSL 下 db 目录必须放在支持 fallocate 的文件系统（tmpfs/ext4），
放在 /mnt/d（v9fs）会以 "Fail to fallocate block file" 退出。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=os.environ.get("SEEKDB_PATH", "/dev/shm/coursecite-smoke"))
    args = ap.parse_args()

    try:
        import pyseekdb
    except ImportError as exc:
        print(f"缺少 pyseekdb：{exc}\n请先 `pip install -e '.[live]'`", file=sys.stderr)
        return 2

    try:
        client = pyseekdb.Client(path=args.path)
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
    print(f"count={col.count()} path={args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
