from __future__ import annotations

import re
from pathlib import Path

from conftest import PROJECT_ROOT, SNAPSHOT, SRC, WEB

CORE = SRC / "coursecite" / "core"
PORTS = SRC / "coursecite" / "ports"
ADAPTERS = SRC / "coursecite" / "adapters"
SITE = PROJECT_ROOT.parent / "site"  # Qoder Sites 发布目录（web/ 的构建产物）

BANNED_IN_CORE_PORTS = ("pyseekdb", "pymysql", "fastapi", "uvicorn", "adapters", "snapshot")


def _import_lines(directory: Path) -> list[str]:
    lines: list[str] = []
    for path in directory.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                lines.append(f"{path.name}: {stripped}")
    return lines


def test_core_and_ports_have_no_storage_or_web_imports():
    """判据 A：换适配器时 core/ports 的 import 行不需要变。"""
    for directory in (CORE, PORTS):
        for line in _import_lines(directory):
            lowered = line.lower()
            for banned in BANNED_IN_CORE_PORTS:
                assert banned not in lowered, f"{directory.name}/ 出现被禁依赖：{line}"


def test_core_does_not_know_snapshot_word():
    for path in CORE.rglob("*.py"):
        assert "snapshot" not in path.read_text(encoding="utf-8").lower(), f"core 里出现 snapshot：{path}"


def test_frontend_has_no_ranking_logic():
    """判据 C：静态前端目录里不出现排序/融合关键词，也不出现数组排序调用。

    同时扫源目录 `web/` 和发布产物 `site/`，防止构建后漂移。
    """
    banned = re.compile(r"rrf|recall|fusion|weight_sum", re.IGNORECASE)
    frontends = [WEB] + ([SITE] if (SITE / "index.html").is_file() else [])
    for frontend in frontends:
        for path in frontend.rglob("*"):
            if path.is_file() and path.suffix in {".js", ".html", ".css"}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                assert not banned.search(text), f"{path} 出现排序/融合关键词"
        app_js = frontend / "static" / "app.js"
        if app_js.is_file():
            assert ".sort(" not in app_js.read_text(encoding="utf-8")


def test_cli_limit_default_comes_from_policy():
    """CLI 的 --limit 默认值必须来自 core/policy.py，不能写死第二个 8。"""
    from coursecite.cli import build_parser
    from coursecite.core.policy import DEFAULT_TOP_K

    args = build_parser().parse_args(["search", "任意查询"])
    assert args.limit == DEFAULT_TOP_K


def test_top_k_defined_once():
    """判据 D：最终 top-k 只在 core/policy.py 定义一次。"""
    definitions = []
    for directory in (CORE, PORTS, ADAPTERS):
        for path in directory.rglob("*.py"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if re.match(r"\s*DEFAULT_TOP_K\s*=", line):
                    definitions.append(f"{path.relative_to(PROJECT_ROOT)}: {line.strip()}")
    assert definitions == ["src/coursecite/core/policy.py: DEFAULT_TOP_K = 8"], definitions


def test_both_adapters_satisfy_the_same_store_port():
    """需求：LiveStore 与 SnapshotStore 实现同一个 StorePort。"""
    from coursecite.adapters.live_store import LiveStore
    from coursecite.adapters.snapshot_store import SnapshotStore
    from coursecite.ports.store import StorePort

    assert isinstance(SnapshotStore(SNAPSHOT), StorePort)
    # LiveStore 连库才能实例化；这里只核对协议要求的属性/方法都在类上。
    for name in StorePort.__protocol_attrs__:  # type: ignore[attr-defined]
        assert hasattr(LiveStore, name), f"LiveStore 缺少 StorePort.{name}"
