"""Run the three-turn demo against a deployed Dify app (service API).

This is the "real Dify" counterpart to ``samples/replay.py``.  It requires your
own app API key; nothing is hardcoded:

    export DIFY_BASE_URL=http://127.0.0.1:8088
    export DIFY_APP_KEY=app-...
    python3 tools/dify_smoke.py

It writes ``samples/dify_run.jsonl`` (query/answer/conversation_id, no secrets)
and exits non-zero if memory does not accumulate across turns.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
TURNS = [
    "我把盐当成糖放进了阿福的茶里",
    "我又把你的板凳腿锯短了一截",
    "今天茶里我什么都没放，真的。",
]


def chat(base_url: str, app_key: str, query: str, conversation_id: str = "") -> dict:
    payload = json.dumps(
        {
            "inputs": {},
            "query": query,
            "response_mode": "blocking",
            "conversation_id": conversation_id,
            "user": "dify-smoke",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/chat-messages",
        data=payload,
        headers={"Authorization": f"Bearer {app_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("DIFY_BASE_URL", "http://127.0.0.1:8088"))
    parser.add_argument("--app-key", default=os.environ.get("DIFY_APP_KEY", ""))
    parser.add_argument("--out", default=str(ROOT / "samples" / "dify_run.jsonl"))
    args = parser.parse_args(argv)
    if not args.app_key:
        print("DIFY_APP_KEY is required (create one in Dify: app -> API Access)", file=sys.stderr)
        return 2

    records: list[dict] = []
    conversation_id = ""
    for index, query in enumerate(TURNS, start=1):
        try:
            response = chat(args.base_url, args.app_key, query, conversation_id)
        except urllib.error.HTTPError as error:
            print(f"turn {index} failed: HTTP {error.code} {error.read()[:400]!r}", file=sys.stderr)
            return 1
        conversation_id = response.get("conversation_id") or conversation_id
        answer = response.get("answer", "")
        records.append(
            {
                "turn": index,
                "query": query,
                "conversation_id": conversation_id,
                "answer": answer,
                "message_id": response.get("message_id"),
            }
        )
        print(f"--- turn {index} ---")
        print(answer)
        print()
        if f"{index} 笔账" not in answer:
            print(f"turn {index}: expected {index} remembered entries in the answer", file=sys.stderr)
            return 1
        if index > 1 and TURNS[0] not in answer:
            print(f"turn {index}: first-turn memory was not recalled", file=sys.stderr)
            return 1

    out = pathlib.Path(args.out)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    print(f"[dify-smoke] wrote {out.name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
