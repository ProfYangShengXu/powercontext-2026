"""唯一装配点：读环境 -> 选一个 StorePort 实现 -> 注入 core。

禁止在 UI 或 core 里判断"当前是线上还是本地"。
选不到实现就响亮报错，绝不静默降级。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..adapters.live_store import LiveStore
from ..adapters.snapshot_store import SnapshotStore
from ..config import ConfigError, Settings
from ..core.service import IngestService, SearchService
from ..ports.store import StorePort


@dataclass
class App:
    settings: Settings
    store: StorePort
    search: SearchService
    ingest: IngestService


def build_store(settings: Settings) -> StorePort:
    if settings.store == "live":
        return LiveStore(settings)
    if settings.store == "snapshot":
        return SnapshotStore(settings.snapshot_path)
    raise ConfigError(f"未知的 COURSECITE_STORE={settings.store!r}（只支持 live / snapshot）")


def build_app(settings: Settings | None = None) -> App:
    settings = settings or Settings.from_env()
    store = build_store(settings)
    return App(
        settings=settings,
        store=store,
        search=SearchService(store),
        ingest=IngestService(store),
    )
