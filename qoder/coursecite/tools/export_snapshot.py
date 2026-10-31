#!/usr/bin/env python3
"""从 LiveStore 导出前端快照（含 data/expected/ 下的预期结果）。

用法：
    export COURSECITE_STORE=live SEEKDB_PATH=/dev/shm/coursecite-db
    python tools/export_snapshot.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from coursecite.bootstrap.main import build_app  # noqa: E402
from coursecite.config import Settings  # noqa: E402
from coursecite.export import export_snapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", default=str(PROJECT / "data" / "queries.json"))
    parser.add_argument("--out", default=str(PROJECT / "data" / "snapshots" / "snapshots.json"))
    parser.add_argument("--expected", default=str(PROJECT / "data" / "expected"))
    args = parser.parse_args()

    settings = Settings.from_env()
    if settings.store != "live":
        print("导出快照需要 LiveStore：export COURSECITE_STORE=live", file=sys.stderr)
        return 2
    app = build_app(settings)
    snapshot = export_snapshot(app, args.queries, args.out, expected_dir=args.expected)
    print(f"已导出 {len(snapshot['queries'])} 条查询 -> {args.out}")
    print(f"索引：{snapshot['index']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
