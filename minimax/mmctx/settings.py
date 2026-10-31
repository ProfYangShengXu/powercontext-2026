"""配置：装配点读环境变量，适配器不自己读环境。"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> None:
    """极简 .env 读取：KEY=VALUE，已存在的环境变量优先。"""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def default_seekdb_path() -> Path:
    """嵌入式 seekdb 需要真正的 Linux 文件系统。

    在 WSL 下 /mnt/d 这类 Windows 盘（9p）打不开 seekdb 数据目录，
    /mnt/wsl 是 tmpfs，可直接用；其他 Linux 机器回落到系统临时目录。
    """
    configured = os.environ.get("MMCTX_SEEKDB_PATH")
    if configured:
        return Path(configured).expanduser()
    wsl_tmp = Path("/mnt/wsl")
    if wsl_tmp.is_dir() and os.access(wsl_tmp, os.W_OK):
        return wsl_tmp / "mmctx-seekdb"
    return Path(tempfile.gettempdir()) / "mmctx-seekdb"


@dataclass(frozen=True)
class Settings:
    root: Path
    seekdb_path: Path
    pc_server_url: str
    pc_scope_title: str
    pc_scope_id: str | None
    pc_timeout_s: float
    minimax_api_key: str | None
    minimax_base_url: str
    minimax_chat_path: str
    minimax_model: str
    minimax_timeout_s: float
    minimax_max_completion_tokens: int
    budget_tokens: int
    embedding_dim: int

    @property
    def demo_project(self) -> Path:
        return self.root / "demo_project"

    @property
    def corpus_dir(self) -> Path:
        return self.root / "corpus"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    @property
    def devlog_dir(self) -> Path:
        return self.root / "devlog"

    @property
    def samples_dir(self) -> Path:
        return self.root / "samples"


def build_settings(root: Path | None = None) -> Settings:
    root = (root or ROOT).resolve()
    load_env_file(root / ".env")
    return Settings(
        root=root,
        seekdb_path=default_seekdb_path(),
        pc_server_url=os.environ.get("PC_SERVER_URL", "http://127.0.0.1:8000").rstrip("/"),
        pc_scope_title=os.environ.get("PC_SCOPE_TITLE", "mmctx-competition"),
        pc_scope_id=os.environ.get("PC_SCOPE_ID") or None,
        pc_timeout_s=float(os.environ.get("PC_TIMEOUT_S", "15")),
        minimax_api_key=os.environ.get("MINIMAX_API_KEY") or None,
        minimax_base_url=os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.chat/v1").rstrip("/"),
        minimax_chat_path=os.environ.get("MINIMAX_CHAT_PATH", "/text/chatcompletion_v2"),
        minimax_model=os.environ.get("MINIMAX_MODEL", "MiniMax-Text-01"),
        minimax_timeout_s=float(os.environ.get("MINIMAX_TIMEOUT_S", "60")),
        minimax_max_completion_tokens=int(os.environ.get("MINIMAX_MAX_COMPLETION_TOKENS", "8192")),
        budget_tokens=int(os.environ.get("MMCTX_BUDGET_TOKENS", "12000")),
        embedding_dim=int(os.environ.get("MMCTX_EMBEDDING_DIM", "384")),
    )
