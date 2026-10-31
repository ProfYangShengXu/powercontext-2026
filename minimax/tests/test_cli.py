"""CLI 与分层判据：core/ports 不得 import 适配器或 SDK。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mmctx.cli", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )


def test_layering_check_passes():
    result = _run("check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"ok": true' in result.stdout


def test_cli_help_lists_commands():
    result = subprocess.run(
        [sys.executable, "-m", "mmctx.cli", "--help"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    for command in ("import", "context", "run", "test", "state", "resume", "verify"):
        assert command in result.stdout
