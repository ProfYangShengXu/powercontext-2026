"""Replay the demo exactly as the Dify Chatflow would call the sidecar.

For every turn it performs the same three calls the DSL makes:
  1. POST /memory/write   (raw text, X-Session-Id)
  2. POST /memory/search  (raw text, X-Session-Id)
  3. POST /memory/list    (X-Session-Id)
then runs the *same* ``main`` function that is embedded in the Dify Code node.

Usage:
    python3 samples/replay.py --run-id readme
    python3 samples/replay.py --out samples/replay_output.jsonl --transcript samples/transcript.md

Exit code is non-zero if a later turn cannot see earlier memories, or if the
service starts inventing answers instead of failing.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sidecar"))

from npc_persona import main as compose_reply  # noqa: E402


def call(base_url: str, path: str, session: str, body: str = "", timeout: float = 20.0) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body.encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8", "X-Session-Id": session},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        try:
            return error.code, json.loads(raw)
        except ValueError:
            return error.code, {"ok": False, "error": {"code": "non_json", "message": raw[:300]}}
    except (urllib.error.URLError, OSError) as error:
        return 0, {"ok": False, "error": {"code": "sidecar_unreachable", "message": str(error)}}


def run(base_url: str, demo_path: pathlib.Path, run_id: str) -> list[dict]:
    records: list[dict] = []
    seen: dict[str, list[str]] = {}
    failures: list[str] = []
    for line_no, line in enumerate(demo_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        short_session = str(entry["session"])
        session = f"{short_session}--{run_id}"
        text = str(entry["input"])
        inputs = seen.setdefault(short_session, [])

        status_w, write = call(base_url, "/memory/write", session, text)
        status_s, search = call(base_url, "/memory/search", session, text)
        status_l, inventory = call(base_url, "/memory/list", session)

        if not (status_w == status_s == status_l == 200 and write.get("ok") and inventory.get("ok")):
            failures.append(f"line {line_no}: sidecar/PowerContext call failed: {write} {search} {inventory}")
            records.append({"line": line_no, "session": session, "input": text, "error": True,
                            "write": write, "search": search, "list": inventory})
            continue

        reply = compose_reply(
            json.dumps(write, ensure_ascii=False),
            json.dumps(search, ensure_ascii=False),
            json.dumps(inventory, ensure_ascii=False),
            text,
        )

        memories = [item["text"] for item in inventory["memories"]]
        is_duplicate = bool(write.get("duplicate"))
        if line_no and short_session in seen:
            for earlier in inputs:
                if earlier != text and earlier not in memories:
                    failures.append(f"line {line_no}: earlier memory not recalled: {earlier!r}")
        previous_count = len(inputs)
        if not is_duplicate and inventory["count"] <= previous_count:
            failures.append(
                f"line {line_no}: memory count did not grow ({previous_count} -> {inventory['count']})"
            )
        inputs.append(text)

        records.append(
            {
                "line": line_no,
                "session_key": short_session,
                "session": session,
                "input": text,
                "write": write,
                "search": search,
                "list": inventory,
                "reply": reply["reply"],
                "memory_count": reply["memory_count"],
                "is_duplicate": reply["is_duplicate"],
                "trace": reply["trace"],
            }
        )
    if failures:
        print("REPLAY FAILED:", file=sys.stderr)
        for failure in failures:
            print(" -", failure, file=sys.stderr)
        raise SystemExit(1)
    return records


def render_transcript(records: list[dict], run_id: str) -> str:
    lines = [
        "# 回放记录（真实 sidecar + 真实 PowerContext）",
        "",
        f"- 运行 ID：`{run_id}`；每个会话映射到独立的 PowerContext scope",
        "- 三条调用与 DSL 完全一致：`/memory/write` → `/memory/search` → `/memory/list`，再用 Code 节点同一份 `main()` 合成回复",
        "",
    ]
    current = None
    for record in records:
        if record["session_key"] != current:
            current = record["session_key"]
            lines.extend([f"## 会话 `{current}`", ""])
        lines.append(f"**你**：{record['input']}")
        lines.append("")
        lines.append(record["reply"])
        lines.append("")
        lines.append(
            f"> 本轮记忆数：{record['memory_count']}；检索命中：{record['search'].get('count', 0)}；"
            f"重复写入：{'是' if record['is_duplicate'] else '否'}"
        )
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--demo", default=str(ROOT / "samples" / "demo.jsonl"))
    parser.add_argument("--out", default=str(ROOT / "samples" / "replay_output.jsonl"))
    parser.add_argument("--transcript", default=str(ROOT / "samples" / "transcript.md"))
    parser.add_argument("--run-id", default=time.strftime("run-%Y%m%d-%H%M%S"))
    args = parser.parse_args(argv)

    records = run(args.base_url, pathlib.Path(args.demo), args.run_id)
    out = pathlib.Path(args.out)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    transcript = render_transcript(records, args.run_id)
    pathlib.Path(args.transcript).write_text(transcript, encoding="utf-8")
    print(transcript)
    print(f"\n[replay] wrote {out.name} and {pathlib.Path(args.transcript).name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
