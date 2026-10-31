"""命令行入口：ingest / search / log / serve / snapshot。"""

from __future__ import annotations

import argparse
import json
import sys

from .bootstrap.main import build_app
from .config import ConfigError, Settings
from .core.retrieval import format_hits_table
from .core.service import SearchService
from .export import export_snapshot
from .ports.store import NoMatch, StoreError, StoreUnavailable

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_NO_MATCH = 3
EXIT_STORE = 4


def _fail(message: str, code: int) -> int:
    print(message, file=sys.stderr)
    return code


def _live_only(settings: Settings, action: str) -> int | None:
    if settings.store != "live":
        return _fail(
            f"{action} 需要 LiveStore（真查 seekdb）。\n"
            f"当前 COURSECITE_STORE={settings.store}。\n"
            f"请执行：export COURSECITE_STORE=live 并连上 seekdb（见 README）。\n"
            f"快照模式是只读回放，不会假装写入。",
            EXIT_CONFIG,
        )
    return None


def _cmd_ingest(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    blocked = _live_only(settings, "ingest")
    if blocked is not None:
        return blocked
    app = build_app(settings)
    try:
        report = app.ingest.ingest(args.data or str(settings.data_dir), rebuild=args.rebuild)
    except StoreError as exc:
        return _fail(str(exc), EXIT_STORE)
    print(
        f"入库完成：文件 {report.files}，段落 {report.chunks}，写入 {report.written}，"
        f"重复跳过 {report.duplicates}"
    )
    print(f"课程：{', '.join(report.courses) or '（无）'}")
    if report.skipped:
        print("跳过：")
        for item in report.skipped:
            print(f"  - {item}")
    return EXIT_OK


def _cmd_search(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    app = build_app(settings)
    try:
        response = app.search.search(
            args.query,
            course=args.course,
            max_lecture=args.lecture_max,
            limit=args.limit,
        )
    except NoMatch as exc:
        return _fail(str(exc), EXIT_NO_MATCH)
    except StoreUnavailable as exc:
        return _fail(str(exc), EXIT_CONFIG)
    except StoreError as exc:
        return _fail(str(exc), EXIT_STORE)
    if args.json:
        print(json.dumps(response.to_dict(include_text=False), ensure_ascii=False, indent=2))
    else:
        print(format_hits_table(response), end="")
    if args.show_sql:
        sql = getattr(app.store, "last_sql", lambda: None)()
        if sql:
            print("-- 实际发给 seekdb 的混合检索 SQL --")
            print(sql)
        else:
            print("-- 当前存储实现没有可展示的 SQL（快照模式不连库）--")
    return EXIT_OK


def _cmd_log(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    app = build_app(settings)
    try:
        records = app.search.history(args.limit)
    except StoreError as exc:
        return _fail(str(exc), EXIT_STORE)
    if args.json:
        print(json.dumps([r.to_dict() for r in records], ensure_ascii=False, indent=2))
        return EXIT_OK
    if not records:
        print("retrieval_log 还是空的：先跑一次 search。")
        return EXIT_OK
    print(f"最近 {len(records)} 条检索记录：")
    for record in records:
        print(
            f"- {record.created_at}  [{record.store_mode}]  {record.query!r}  "
            f"filters={record.filters.to_dict()}  {record.elapsed_ms}ms  "
            f"top={len(record.top_ids)} 条"
        )
    return EXIT_OK


def _cmd_snapshot(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    blocked = _live_only(settings, "snapshot export")
    if blocked is not None:
        return blocked
    app = build_app(settings)
    try:
        snapshot = export_snapshot(app, args.queries, args.out, expected_dir=args.expected)
    except StoreError as exc:
        return _fail(str(exc), EXIT_STORE)
    print(f"已导出 {len(snapshot['queries'])} 条查询 -> {args.out}")
    return EXIT_OK


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .web import create_app

    settings = Settings.from_env()
    app = build_app(settings)
    asgi = create_app(app.search, settings)
    print(f"课引 CourseCite: http://{args.host}:{args.port}  （store={settings.store}）")
    uvicorn.run(asgi, host=args.host, port=args.port, log_level="warning")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coursecite", description="课引 CourseCite：课程资料混合检索 + 引用定位")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="扫描讲义并幂等入库（仅 LiveStore）")
    p.add_argument("--data", default=None, help="语料目录（默认 COURSECITE_DATA 或 data/samples）")
    p.add_argument("--rebuild", action="store_true", help="先清空索引再入库")
    p.set_defaults(func=_cmd_ingest)

    p = sub.add_parser("search", help="一句话检索，返回原文段落 + 出处")
    p.add_argument("query")
    p.add_argument("--course", default=None)
    p.add_argument("--lecture-max", dest="lecture_max", type=int, default=None)
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--json", action="store_true")
    p.add_argument("--show-sql", dest="show_sql", action="store_true", help="打印实际发给 seekdb 的 SQL")
    p.set_defaults(func=_cmd_search)

    p = sub.add_parser("log", help="查看检索留痕（retrieval_log）")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_log)

    p = sub.add_parser("snapshot", help="从 LiveStore 导出前端快照 JSON")
    p.add_argument("--queries", default="data/queries.json")
    p.add_argument("--out", default="data/snapshots/snapshots.json")
    p.add_argument("--expected", default="data/expected")
    p.set_defaults(func=_cmd_snapshot)

    p = sub.add_parser("serve", help="启动 Web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=_cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        return _fail(f"配置错误：{exc}", EXIT_CONFIG)
    except StoreUnavailable as exc:
        return _fail(str(exc), EXIT_CONFIG)
    except StoreError as exc:
        return _fail(str(exc), EXIT_STORE)


if __name__ == "__main__":
    raise SystemExit(main())
