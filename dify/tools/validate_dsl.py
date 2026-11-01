"""Validate the generated Dify DSL against the real graphon 0.8.0 node models.

Dify 1.18 delegates built-in workflow nodes to ``graphon==0.8.0``.  Importing the
same package lets us catch schema mistakes (wrong body type, missing answer
template, bad retry fields) before the reviewer ever opens the Dify UI.

Usage:
    python3 tools/validate_dsl.py [path/to/dsl.yml]
"""

from __future__ import annotations

import pathlib
import sys

import yaml

from graphon.nodes.answer.entities import AnswerNodeData
from graphon.nodes.code.entities import CodeNodeData
from graphon.nodes.end.entities import EndNodeData
from graphon.nodes.http_request.entities import HttpRequestNodeData
from graphon.nodes.start.entities import StartNodeData

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_DSL = ROOT / "dify_tavern_npc.yml"

NODE_MODELS = {
    "start": StartNodeData,
    "http-request": HttpRequestNodeData,
    "code": CodeNodeData,
    "answer": AnswerNodeData,
    "end": EndNodeData,
}


def validate(path: pathlib.Path) -> list[str]:
    problems: list[str] = []
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        return ["top level is not a mapping"]

    if document.get("version") != "0.7.0":
        problems.append(f"version must be the string '0.7.0', got {document.get('version')!r}")
    if document.get("kind") != "app":
        problems.append("kind must be 'app'")
    app = document.get("app") or {}
    if app.get("mode") not in ("advanced-chat", "workflow"):
        problems.append(f"unexpected app.mode {app.get('mode')!r}")
    workflow = document.get("workflow") or {}
    graph = workflow.get("graph") or {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    node_ids = {node.get("id") for node in nodes}
    if len(node_ids) != len(nodes):
        problems.append("duplicate node ids")
    if len({"start", "end", "answer"} & node_ids) == 0:
        problems.append("missing start/terminal node")

    types = [node.get("data", {}).get("type") for node in nodes]
    if app.get("mode") == "advanced-chat" and "answer" not in types:
        problems.append("advanced-chat requires an answer node")
    if types.count("http-request") < 2:
        problems.append(f"need at least two http-request nodes, found {types.count('http-request')}")
    if "code" not in types:
        problems.append("need the compose code node")

    for node in nodes:
        node_id = node.get("id")
        data = node.get("data") or {}
        node_type = data.get("type")
        model = NODE_MODELS.get(node_type)
        if model is None:
            problems.append(f"{node_id}: unvalidated node type {node_type!r}")
            continue
        try:
            parsed = model.model_validate(data)
        except Exception as error:  # noqa: BLE001 - report every schema failure
            problems.append(f"{node_id}: {type(error).__name__}: {error}")
            continue
        if node_type == "http-request":
            if parsed.method.upper() != "POST":
                problems.append(f"{node_id}: method should be POST")
            if not parsed.retry_config.retry_enabled:
                problems.append(f"{node_id}: retry must be enabled so non-2xx fails the run")
            if "X-Session-Id: {{#sys.conversation_id#}}" not in parsed.headers:
                problems.append(f"{node_id}: missing X-Session-Id header binding")
            if parsed.body is None:
                problems.append(f"{node_id}: body is required")
        if node_type == "code":
            if "def main(" not in parsed.code:
                problems.append(f"{node_id}: code has no main()")
            if set(parsed.outputs) < {"reply", "memory_count", "is_duplicate", "trace"}:
                problems.append(f"{node_id}: missing declared outputs")
            try:
                namespace: dict = {}
                exec(compile(parsed.code, f"<{node_id}>", "exec"), namespace)  # noqa: S102 - our own DSL
                result = namespace["main"](
                    '{"ok":true,"scope_id":"scp_demo","revision":1,"entry_id":"e1","duplicate":false,"text":"x"}',
                    '{"ok":true,"scope_id":"scp_demo","mode":"fts","count":1,"hits":[{"text":"x","score":1.0}]}',
                    '{"ok":true,"scope_id":"scp_demo","revision":1,"count":1,'
                    '"memories":[{"text":"x","kind":"prank","entry_id":"e1","version":1,"state":"active"}]}',
                    "x",
                )
                if not isinstance(result.get("reply"), str) or result.get("memory_count") != 1:
                    problems.append(f"{node_id}: main() returned an unexpected shape: {result}")
            except Exception as error:  # noqa: BLE001 - surface any sandbox-code failure
                problems.append(f"{node_id}: embedded code failed to run: {type(error).__name__}: {error}")

    for edge in edges:
        if edge.get("source") not in node_ids:
            problems.append(f"edge {edge.get('id')}: unknown source {edge.get('source')}")
        if edge.get("target") not in node_ids:
            problems.append(f"edge {edge.get('id')}: unknown target {edge.get('target')}")

    env_names = {item.get("name") for item in workflow.get("environment_variables") or []}
    if "SIDECAR_BASE_URL" not in env_names:
        problems.append("missing SIDECAR_BASE_URL environment variable")
    for node in nodes:
        if "{{#env.SIDECAR_BASE_URL#}}" in (node.get("data", {}).get("url") or ""):
            break
    else:
        problems.append("no node references {{#env.SIDECAR_BASE_URL#}}")

    return problems


def main(argv: list[str] | None = None) -> int:
    args = (argv if argv is not None else sys.argv[1:])
    path = pathlib.Path(args[0]) if args else DEFAULT_DSL
    problems = validate(path)
    if problems:
        print(f"INVALID {path}:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    nodes = document["workflow"]["graph"]["nodes"]
    http_nodes = sum(1 for n in nodes if n["data"]["type"] == "http-request")
    print(f"OK {path.name}: {len(nodes)} nodes ({http_nodes} HTTP), graphon 0.8.0 schema validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
