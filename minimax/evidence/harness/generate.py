"""MiniMax 生成代码的取证脚本（可复跑）。

用途：把 evidence/prompt-0N.md 作为请求体发给 MiniMax，保存请求体、响应原文，
并把回复里的 ```python 代码块**直接写入**目标文件。写入动作由本脚本完成，
不是人工抄写。

用法（在 minimax/ 下）：
  .venv/bin/python evidence/harness/generate.py \
      --round 01 --prompt evidence/prompt-01.md \
      --target mmctx/adapters/minimax_payload.py

每次运行在 evidence/<round>/ 下保存 request.json / response.json / generated.py。
""" 

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
FENCE = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def extract_python(text: str) -> str:
    blocks = [match.group(1).strip("\n") for match in FENCE.finditer(text)]
    if not blocks:
        raise SystemExit("ERROR: 模型回复里没有 ```python 代码块")
    return max(blocks, key=len) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=0.2)
    args = parser.parse_args()

    env = {**load_env(ENV_FILE), **os.environ}
    key = env.get("MINIMAX_API_KEY", "")
    if not key:
        raise SystemExit("ERROR: 缺少 MINIMAX_API_KEY（.env）")
    base = env.get("MINIMAX_BASE_URL", "").rstrip("/")
    path = env.get("MINIMAX_CHAT_PATH", "/text/chatcompletion_v2")
    model = args.model or env.get("MINIMAX_MODEL", "MiniMax-Text-01")
    max_tokens = args.max_tokens or int(env.get("MINIMAX_MAX_COMPLETION_TOKENS", "8192"))

    prompt = Path(args.prompt).read_text(encoding="utf-8")
    out_dir = ROOT / "evidence" / args.round
    out_dir.mkdir(parents=True, exist_ok=True)

    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": args.temperature,
        "stream": False,
    }
    url = base + path
    (out_dir / "request.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "method": "POST",
                "url": url,
                "headers": {"Authorization": "Bearer <MINIMAX_API_KEY 已省略>", "Content-Type": "application/json"},
                "body": body,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    started = time.perf_counter()
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
        timeout=180.0,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text[:2000]}
    (out_dir / "response.json").write_text(
        json.dumps(
            {"http_status": response.status_code, "latency_ms": latency_ms, "body": payload},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    base_resp = payload.get("base_resp") or {}
    if response.status_code != 200 or int(base_resp.get("status_code", 0) or 0) != 0:
        print(f"ERROR: http={response.status_code} base_resp={base_resp}")
        return 4
    reply = payload["choices"][0]["message"]["content"]
    (out_dir / "reply.txt").write_text(reply, encoding="utf-8")
    code = extract_python(reply)
    (out_dir / "generated.py").write_text(code, encoding="utf-8")
    target = ROOT / args.target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code, encoding="utf-8")

    usage = payload.get("usage") or {}
    (out_dir / "meta.json").write_text(
        json.dumps(
            {
                "model": payload.get("model", model),
                "base_url": base,
                "path": path,
                "http_status": response.status_code,
                "latency_ms": latency_ms,
                "usage": usage,
                "finish_reason": payload["choices"][0].get("finish_reason"),
                "target": args.target,
                "bytes_written": len(code.encode("utf-8")),
                "request_id": response.headers.get("x-request-id", ""),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"ok": True, "target": args.target, "bytes": len(code.encode("utf-8")),
                      "usage": usage, "latency_ms": latency_ms}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
