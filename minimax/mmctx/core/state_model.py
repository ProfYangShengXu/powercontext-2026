"""任务状态的形状与新鲜度判断。字节上限不在这里（那是适配器的实现约束）。"""

from __future__ import annotations

import json

MAX_TODO_ITEMS = 20
MAX_TODO_CHARS = 200


def build_state(
    *,
    task_id: str,
    title: str,
    run_id: str,
    spec_digest: str,
    context_sha256: str,
    applied_commit: str | None,
    status: str,
    todo: list[str],
    seq: int,
    updated_at: str,
) -> dict[str, object]:
    items = [item[:MAX_TODO_CHARS] for item in todo[:MAX_TODO_ITEMS]]
    return {
        "schema": 1,
        "task_id": task_id,
        "title": title,
        "run_id": run_id,
        "spec_digest": spec_digest,
        "context_sha256": context_sha256,
        "applied_commit": applied_commit,
        "status": status,
        "todo": items,
        "seq": seq,
        "updated_at": updated_at,
    }


def pack_state(state: dict[str, object]) -> str:
    """规范 JSON：键排序、紧凑分隔、中文原样。"""
    return json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_state(text: str) -> dict[str, object]:
    data = json.loads(text)
    if not isinstance(data, dict) or "task_id" not in data:
        raise ValueError("state text is not a task state object")
    return data


def state_hash(state: dict[str, object]) -> str:
    """状态指纹：用于「同一 task 重复 state 不新增记录」的幂等判断。"""
    from mmctx.core.hashing import sha256_hex

    stable = {key: value for key, value in state.items() if key not in ("seq", "updated_at")}
    return sha256_hex(pack_state(stable), 32)


def is_stale(state: dict[str, object], current_spec_digest: str) -> bool:
    return str(state.get("spec_digest", "")) != current_spec_digest
