"""The tavern NPC's reply composer.

This module is the single source of truth for the deterministic ``main`` function
that is embedded verbatim into the Dify Code node (see ``tools/build_dsl.py``)
and imported by ``samples/replay.py``.  Keeping one implementation means the
three sample transcripts are produced by exactly the same code Dify runs.

The function deliberately uses only ``json`` so the Dify sandbox can execute it.
It raises on malformed input instead of inventing an answer: a broken recall
must fail the run, not be papered over.
"""

import json

MAX_LISTED_MEMORIES = 8


def _load(raw, label: str) -> dict:
    if raw is None or str(raw).strip() == "":
        raise ValueError(f"{label} response is empty; the sidecar did not answer")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} response is not JSON: {str(raw)[:160]!r}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} response is not a JSON object")
    if value.get("ok") is False:
        error = value.get("error") or {}
        raise ValueError(f"{label} failed: {error.get('code')}: {error.get('message')}")
    return value


def _short(text: str, limit: int = 28) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def main(write_body: str, search_body: str, list_body: str, query: str) -> dict:
    written = _load(write_body, "memory/write")
    found = _load(search_body, "memory/search")
    inventory = _load(list_body, "memory/list")

    memories = inventory.get("memories") or []
    total = int(inventory.get("count") or len(memories))
    revision = inventory.get("revision")
    scope_id = inventory.get("scope_id") or written.get("scope_id") or "?"
    duplicate = bool(written.get("duplicate"))
    hits = found.get("hits") or []
    query = (query or "").strip() or str(written.get("text") or "").strip()

    lines: list[str] = []
    lines.append("（阿福把抹布往肩上一搭，斜眼看你）")
    lines.append("「又——是——你。」")
    if total == 0:
        lines.append("「今天还没人整我。你是第一个？」")
    else:
        lines.append(f"「你在我这儿留下的 {total} 笔账，我可一件没忘：」")
        shown = memories[:MAX_LISTED_MEMORIES]
        for index, memory in enumerate(shown, start=1):
            lines.append(f"  {index}. {_short(memory.get('text', ''), 40)}")
        if total > len(shown):
            lines.append(f"  ……还有 {total - len(shown)} 笔，我懒得念了。")

    if total <= 1:
        lines.append("「第一次，我当你手滑。」")
    elif total == 2:
        lines.append("「第二次，我当你喝多了。」")
    elif total == 3:
        lines.append("「第三次……你猜我为什么把茶壶换成了铁的？」")
    elif total <= 5:
        lines.append(f"「第 {total} 次了。我已经把扫帚靠在门后了。」")
    else:
        lines.append(f"「{total} 次了。今天这顿茶钱翻倍，抹布你自己洗。」")

    if duplicate:
        lines.append(f"「『{_short(query)}』这句你上次就说过——我记着呢，别想赖账。」")

    relevant = [hit for hit in hits if str(hit.get("text", "")).strip() != query]
    if relevant:
        lines.append(f"「上次你提『{_short(relevant[0].get('text', ''))}』的时候，我就该防着你。」")

    lines.append(
        f"（PowerContext · scope {str(scope_id)[:16]}… · 记忆 revision {revision} · "
        f"本次检索命中 {len(hits)} 条）"
    )

    trace = json.dumps(
        {
            "scope_id": scope_id,
            "revision": revision,
            "memory_count": total,
            "search_mode": found.get("mode"),
            "hit_count": len(hits),
            "duplicate": duplicate,
        },
        ensure_ascii=False,
    )
    return {
        "reply": "\n".join(lines),
        "memory_count": total,
        "is_duplicate": duplicate,
        "trace": trace,
    }
