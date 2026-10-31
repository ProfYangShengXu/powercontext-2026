#!/usr/bin/env python3
"""把 web/ + snapshots.json 打包成纯静态站点（Qoder Sites 可直接上传）。

产物：qoder/site/{index.html, static/, data/snapshots.json}

本地预览（必须用 HTTP，file:// 下 fetch 会被浏览器拦）：
    python -m http.server 8000 --directory qoder/site
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
QODER = PROJECT.parent
WEB = PROJECT / "web"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default=str(PROJECT / "data" / "snapshots" / "snapshots.json"))
    parser.add_argument("--out", default=str(QODER / "site"))
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    if not snapshot_path.is_file():
        print(f"缺少快照：{snapshot_path}\n先运行 tools/export_snapshot.py", flush=True)
        return 2
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copytree(WEB / "static", out / "static")

    html = (WEB / "index.html").read_text(encoding="utf-8")
    bootstrap = '<script>window.COURSECITE_SNAPSHOT = "data/snapshots.json";</script>'
    if "<!-- COURSECITE_BOOTSTRAP -->" not in html:
        print("web/index.html 缺少 <!-- COURSECITE_BOOTSTRAP --> 占位", flush=True)
        return 3
    (out / "index.html").write_text(html.replace("<!-- COURSECITE_BOOTSTRAP -->", bootstrap), encoding="utf-8")

    (out / "data").mkdir()
    shutil.copy2(snapshot_path, out / "data" / "snapshots.json")
    print(f"静态站点已生成：{out}")
    print(f"查询 {len(data.get('queries', []))} 条，段落 {data.get('index', {}).get('chunks')} 段")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
