"""运行配置：唯一从环境读取部署差异的地方（除装配点外不读 env）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "samples"
DEFAULT_SNAPSHOT = PROJECT_ROOT / "data" / "snapshots" / "snapshots.json"

VALID_STORES = ("live", "snapshot")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    store: str
    data_dir: Path
    snapshot_path: Path
    collection: str
    seekdb_path: str | None
    seekdb_host: str | None
    seekdb_port: int
    seekdb_user: str | None
    seekdb_password: str
    seekdb_database: str
    embed_cache: Path | None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        store = env.get("COURSECITE_STORE", "snapshot").strip().lower()
        if store not in VALID_STORES:
            raise ConfigError(f"COURSECITE_STORE 只能是 {VALID_STORES}，收到 {store!r}")
        host = (env.get("SEEKDB_HOST") or "").strip() or None
        cache = (env.get("COURSECITE_EMBED_CACHE") or "").strip() or None
        return cls(
            store=store,
            data_dir=Path(env.get("COURSECITE_DATA", str(DEFAULT_DATA_DIR))),
            snapshot_path=Path(env.get("COURSECITE_SNAPSHOT", str(DEFAULT_SNAPSHOT))),
            collection=env.get("SEEKDB_COLLECTION", "cc_chunks"),
            seekdb_path=(env.get("SEEKDB_PATH") or "").strip() or None,
            seekdb_host=host,
            seekdb_port=int(env.get("SEEKDB_PORT", "2881")),
            seekdb_user=(env.get("SEEKDB_USER") or "").strip() or None,
            seekdb_password=env.get("SEEKDB_PASSWORD", ""),
            seekdb_database=env.get("SEEKDB_DATABASE", "test"),
            embed_cache=Path(cache) if cache else None,
        )
