"""bootstrap：唯一装配点。适配器的构造只发生在这里。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mmctx.adapters.gitrepo import Workspace
from mmctx.settings import Settings


@dataclass
class Bundle:
    settings: Settings
    store: object
    state: object
    model: object
    workspace: Workspace


def build_bundle(
    settings: Settings,
    *,
    with_store: bool = True,
    with_state: bool = True,
    with_model: bool = True,
) -> Bundle:
    store = None
    if with_store:
        from mmctx.adapters.seekdb_store import SeekdbStore

        store = SeekdbStore(Path(settings.seekdb_path), embedding_dim=settings.embedding_dim).connect()

    state = None
    if with_state:
        from mmctx.adapters.pc_state import PowerContextState

        state = PowerContextState(
            settings.pc_server_url,
            settings.pc_scope_title,
            scope_id=settings.pc_scope_id,
            timeout_s=settings.pc_timeout_s,
        )

    model = None
    if with_model:
        from mmctx.adapters.minimax_model import MiniMaxModel

        model = MiniMaxModel(
            settings.minimax_api_key,
            settings.minimax_base_url,
            settings.minimax_model,
            chat_path=settings.minimax_chat_path,
            timeout_s=settings.minimax_timeout_s,
        )

    return Bundle(
        settings=settings,
        store=store,
        state=state,
        model=model,
        workspace=Workspace(settings.root),
    )
