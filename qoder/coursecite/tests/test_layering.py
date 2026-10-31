from __future__ import annotations

import re
from pathlib import Path

from conftest import PROJECT_ROOT, SRC, WEB

CORE = SRC / "coursecite" / "core"
PORTS = SRC / "coursecite" / "ports"
ADAPTERS = SRC / "coursecite" / "adapters"

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
    """判据 C：静态前端目录里不出现排序/融合关键词，也不出现数组排序调用。"""
    banned = re.compile(r"rrf|recall|fusion|weight_sum", re.IGNORECASE)
    for path in WEB.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert not banned.search(text), f"{path} 出现排序/融合关键词"
    app_js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert ".sort(" not in app_js


def test_top_k_defined_once():
    """判据 D：最终 top-k 只在 core/policy.py 定义一次。"""
    definitions = []
    for directory in (CORE, PORTS, ADAPTERS):
        for path in directory.rglob("*.py"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if re.match(r"\s*DEFAULT_TOP_K\s*=", line):
                    definitions.append(f"{path.relative_to(PROJECT_ROOT)}: {line.strip()}")
    assert definitions == ["src/coursecite/core/policy.py: DEFAULT_TOP_K = 8"], definitions
