"""Generate the Dify Chatflow DSL from the single-source NPC persona.

Usage:
    python3 tools/build_dsl.py            # writes dify_tavern_npc.yml
    python3 tools/build_dsl.py --check    # exit 1 if the committed file is stale

The generated YAML is validated separately against graphon's node models
(``tools/validate_dsl.py``); this script only assembles the document.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "dify_tavern_npc.yml"
PERSONA = ROOT / "sidecar" / "npc_persona.py"
SIDECAR_URL = "http://host.docker.internal:8787"
SESSION_HEADER = "Content-Type: text/plain; charset=utf-8\nX-Session-Id: {{#sys.conversation_id#}}"


class LiteralDumper(yaml.SafeDumper):
    pass


def _represent_str(dumper: yaml.SafeDumper, data: str):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


LiteralDumper.add_representer(str, _represent_str)


def _start() -> dict:
    return {
        "id": "start",
        "type": "custom",
        "data": {
            "type": "start",
            "title": "开始",
            "desc": "用户对酒馆 NPC 说的话就是本轮输入",
            "variables": [],
        },
        "position": {"x": 40, "y": 300},
        "positionAbsolute": {"x": 40, "y": 300},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
    }


def _http(node_id: str, title: str, path: str, desc: str, body: dict, x: int) -> dict:
    return {
        "id": node_id,
        "type": "custom",
        "data": {
            "desc": desc,
            "title": title,
            "type": "http-request",
            "method": "post",
            "url": "{{#env.SIDECAR_BASE_URL#}}" + path,
            "authorization": {"type": "no-auth", "config": None},
            "headers": SESSION_HEADER,
            "params": "",
            "body": body,
            "timeout": {"connect": 5, "read": 20, "write": 10},
            # Retry is enabled on purpose: with graphon, a non-2xx response only
            # fails the node when retry or error_strategy is set. This makes a
            # stopped sidecar / PowerContext a hard workflow failure.
            "retry_config": {"retry_enabled": True, "max_retries": 1, "retry_interval": 300},
            "ssl_verify": True,
            "variables": [],
        },
        "position": {"x": x, "y": 300},
        "positionAbsolute": {"x": x, "y": 300},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "width": 244,
        "height": 110,
    }


def _code(persona_source: str) -> dict:
    return {
        "id": "compose_reply",
        "type": "custom",
        "data": {
            "desc": "把本轮写入、检索和全部记忆清单合成阿福的回复。任何一侧失败都会抛错，而不是编答案。",
            "title": "阿福的回复",
            "type": "code",
            "code_language": "python3",
            "code": persona_source,
            "variables": [
                {"variable": "write_body", "value_selector": ["write_memory", "body"]},
                {"variable": "search_body", "value_selector": ["search_memory", "body"]},
                {"variable": "list_body", "value_selector": ["list_memory", "body"]},
                {"variable": "query", "value_selector": ["sys", "query"]},
            ],
            "outputs": {
                "reply": {"type": "string", "children": None},
                "memory_count": {"type": "number", "children": None},
                "is_duplicate": {"type": "boolean", "children": None},
                "trace": {"type": "string", "children": None},
            },
        },
        "position": {"x": 1320, "y": 300},
        "positionAbsolute": {"x": 1320, "y": 300},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "width": 244,
        "height": 110,
    }


def _answer() -> dict:
    return {
        "id": "answer",
        "type": "custom",
        "data": {
            "type": "answer",
            "title": "回复",
            "desc": "",
            "answer": "{{#compose_reply.reply#}}",
            "variables": [],
        },
        "position": {"x": 1620, "y": 300},
        "positionAbsolute": {"x": 1620, "y": 300},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
    }


def _edge(source: str, target: str, source_type: str, target_type: str) -> dict:
    return {
        "id": f"{source}-{target}",
        "source": source,
        "target": target,
        "sourceHandle": "source",
        "targetHandle": "target",
        "type": "custom",
        "data": {"isInIteration": False, "isInLoop": False, "sourceType": source_type, "targetType": target_type},
        "zIndex": 0,
    }


def build() -> dict:
    persona_source = PERSONA.read_text(encoding="utf-8")
    nodes = [
        _start(),
        _http(
            "write_memory",
            "① 写入记忆",
            "/memory/write",
            "把本轮用户输入作为一条记忆写进 PowerContext（按会话隔离 scope）",
            {"type": "raw-text", "data": [{"key": "", "type": "text", "value": "{{#sys.query#}}"}]},
            340,
        ),
        _http(
            "search_memory",
            "② 检索记忆",
            "/memory/search",
            "用本轮输入在 PowerContext 中做全文检索，取回相关旧账",
            {"type": "raw-text", "data": [{"key": "", "type": "text", "value": "{{#sys.query#}}"}]},
            640,
        ),
        _http(
            "list_memory",
            "③ 读取全部记忆",
            "/memory/list",
            "从 PowerContext 读取该会话的全部记忆条目，形成账本",
            {"type": "none", "data": []},
            940,
        ),
        _code(persona_source),
        _answer(),
    ]
    edges = [
        _edge("start", "write_memory", "start", "http-request"),
        _edge("write_memory", "search_memory", "http-request", "http-request"),
        _edge("search_memory", "list_memory", "http-request", "http-request"),
        _edge("list_memory", "compose_reply", "http-request", "code"),
        _edge("compose_reply", "answer", "code", "answer"),
    ]
    return {
        "version": "0.7.0",
        "kind": "app",
        "app": {
            "name": "记仇的酒馆 NPC · PowerContext",
            "description": "一个会记住你每次恶作剧的酒馆 NPC。记忆真实写入 PowerContext（按会话分 scope），每轮检索后进入回复。",
            "icon": "🍶",
            "icon_type": "emoji",
            "icon_background": "#FFEAD5",
            "mode": "advanced-chat",
            "use_icon_as_answer_icon": False,
        },
        "dependencies": [],
        "workflow": {
            "conversation_variables": [],
            "environment_variables": [
                {
                    "id": "9c1f6a2e-6f1e-4a6e-9f5e-8d0c1b2a3f40",
                    "name": "SIDECAR_BASE_URL",
                    "value": SIDECAR_URL,
                    "value_type": "string",
                    "description": "sidecar 地址。Dify 在 Docker 里时用 host.docker.internal；sidecar 也在 compose 里时改成 http://sidecar:8787",
                }
            ],
            "features": {
                "file_upload": {"enabled": False},
                "opening_statement": "你是酒馆的常客。阿福是店小二——上次你把他茶里的糖换成盐，他好像还记得。说点什么吧。",
                "retriever_resource": {"enabled": False},
                "sensitive_word_avoidance": {"enabled": False},
                "speech_to_text": {"enabled": False},
                "suggested_questions": [
                    "阿福，今天茶里我什么都没放。",
                    "你板凳腿怎么短了一截？",
                    "我把你的抹布藏到酒窖了。",
                ],
                "suggested_questions_after_answer": {"enabled": False},
                "text_to_speech": {"enabled": False},
            },
            "graph": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.8}},
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if dify_tavern_npc.yml is out of date")
    args = parser.parse_args(argv)
    text = yaml.dump(
        build(),
        Dumper=LiteralDumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=200,
    )
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != text:
            print("dify_tavern_npc.yml is stale; run: python3 tools/build_dsl.py", file=sys.stderr)
            return 1
        print("dify_tavern_npc.yml is up to date")
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
