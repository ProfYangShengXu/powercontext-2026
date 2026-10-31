"""mmctx CLI：import / context / run / test / state / resume / verify / explain。

失败不降级：每一步失败都写 devlog 并返回具名退出码。
3 = 来源反查失败，4 = 模型/补丁失败，5 = 规格已更新，6 = 没有状态，
7 = 状态超上限，8 = seekdb 不可用，9 = PowerContext 不可达。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from mmctx.core.assembly import build_pack, check_expected_sources, query_from_task, render_markdown
from mmctx.core.chunking import chunk_text, split_lines
from mmctx.core.hashing import content_hash, corpus_digest, doc_id_for, sha256_hex
from mmctx.core.patch import TARGET_FILE, build_file_diff, extract_diff, extract_python_file, guard_patch
from mmctx.core.prompts import build_units
from mmctx.core.selection import ensure_required, fuse, mentioned_paths
from mmctx.core.state_model import build_state, is_stale, load_state, pack_state, state_hash
from mmctx.core.verify import VerifyError, require_verified, verify_sources
from mmctx.ports.model import ModelError, ModelRequest, PromptUnit
from mmctx.ports.state import StateTooLargeError
from mmctx.ports.store import Doc, SearchQuery
from mmctx.settings import Settings, build_settings

CORPUS_GLOBS = {
    "spec": ("corpus/specs", "*.md"),
    "code": ("demo_project/shop", "*.py"),
    "test": ("demo_project/tests", "*.py"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


def rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


# --------------------------------------------------------------------------- #
# 语料与任务
# --------------------------------------------------------------------------- #
def corpus_documents(settings: Settings) -> list[tuple[str, str, Path]]:
    documents: list[tuple[str, str, Path]] = []
    for kind, (directory, pattern) in CORPUS_GLOBS.items():
        base = settings.root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.glob(pattern)):
            if not path.is_file():
                continue
            documents.append((kind, rel(path, settings.root), path))
    return documents


def spec_items(settings: Settings) -> list[tuple[str, str]]:
    base = settings.corpus_dir / "specs"
    return [
        (rel(path, settings.root), path.read_text(encoding="utf-8"))
        for path in sorted(base.glob("*.md"))
    ]


def current_spec_digest(settings: Settings) -> str:
    return corpus_digest(spec_items(settings))


def load_task(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"task file is not a mapping: {path}")
    for key in ("task_id", "title"):
        if not str(data.get(key, "")).strip():
            raise ValueError(f"task file missing {key}: {path}")
    data.setdefault("symptoms", "")
    data.setdefault("keywords", [])
    data.setdefault("expected_sources", [])
    return data


def read_source_factory(settings: Settings, rev: str | None = None):
    """读来源原文；给定 rev 时从该提交读（上下文包是历史快照，不能被后续修改变「假」）。"""
    if rev:
        from mmctx.adapters.gitrepo import Workspace

        workspace = Workspace(settings.root)

        def read_at_rev(path: str) -> str:
            return workspace.show(path, rev)

        return read_at_rev

    def read(path: str) -> str:
        return (settings.root / path).read_text(encoding="utf-8")

    return read


def _latest_apply(settings: Settings) -> dict[str, Any] | None:
    """devlog 里最后一条成功的 apply 记录；state 用它对齐 run_id / applied_commit。"""
    best: dict[str, Any] | None = None
    for path in sorted(settings.devlog_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("step") == "apply" and record.get("applied_commit"):
                best = record
    return best


def devlog_write(settings: Settings, run_id: str, record: dict[str, Any]) -> Path:
    settings.devlog_dir.mkdir(parents=True, exist_ok=True)
    path = settings.devlog_dir / f"{run_id}.jsonl"
    payload = {"ts": utc_now(), "run_id": run_id, **record}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #
def cmd_init(args: argparse.Namespace, settings: Settings) -> int:
    from mmctx.bootstrap.main import build_bundle

    bundle = build_bundle(settings)
    try:
        health = bundle.state.health()
        scope = bundle.state.ensure_scope()
        pc_ready = True
    except Exception as error:  # noqa: BLE001 - init 只做体检：PC 没起也要能把 seekdb 建好
        health = {"ready": False, "error": str(error)}
        scope = ""
        pc_ready = False
    stats = bundle.store.stats()
    print_json(
        {
            "seekdb_path": str(settings.seekdb_path),
            "table": stats.table,
            "fulltext_parser": stats.parser,
            "powercontext": {"url": settings.pc_server_url, "ready": pc_ready, "health": health,
                             "scope_id": scope,
                             "hint": "" if pc_ready else "先执行 make serve 再跑 import/state"},
            "minimax": {"base_url": settings.minimax_base_url, "model": settings.minimax_model,
                        "api_key_present": bool(settings.minimax_api_key)},
        }
    )
    return 0


def cmd_import(args: argparse.Namespace, settings: Settings) -> int:
    from mmctx.bootstrap.main import build_bundle
    from mmctx.adapters.seekdb_store import SeekdbStore

    run_id = args.run_id or new_run_id()
    started = time.perf_counter()
    bundle = build_bundle(settings, with_state=not args.no_pc, with_model=False)
    store: SeekdbStore = bundle.store  # type: ignore[assignment]
    if args.reset:
        store.reset()

    documents = corpus_documents(settings)
    total_chunks = 0
    per_kind: dict[str, int] = {}
    for kind, rel_path, path in documents:
        text = path.read_text(encoding="utf-8")
        doc_id = doc_id_for(rel_path)
        chunks = chunk_text(kind, rel_path, text, doc_id)
        doc = Doc(doc_id=doc_id, path=rel_path, kind=kind, content=text, content_hash=content_hash(text), chunks=chunks)
        store.upsert_document(doc)
        total_chunks += len(chunks)
        per_kind[kind] = per_kind.get(kind, 0) + len(chunks)
        if bundle.state is not None:
            bundle.state.capture_source(f"{doc_id}:{content_hash(text)}", text)

    warm = store.warm_vectors()
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    stats = store.stats()
    summary = {
        "run_id": run_id,
        "vector_warm": warm,
        "documents": len(documents),
        "chunks_written": total_chunks,
        "chunks_by_kind": per_kind,
        "db": {"table": stats.table, "chunks": stats.chunks, "current": stats.current_chunks,
               "docs": stats.docs, "parser": stats.parser},
        "spec_digest": current_spec_digest(settings),
        "elapsed_ms": elapsed_ms,
    }
    devlog_write(settings, run_id, {"step": "import", **summary})
    print_json(summary)
    return 0


def cmd_context(args: argparse.Namespace, settings: Settings) -> int:
    from mmctx.bootstrap.main import build_bundle
    from mmctx.adapters.seekdb_store import StoreUnavailableError

    run_id = args.run_id or new_run_id()
    task = load_task(Path(args.task))
    started = time.perf_counter()
    bundle = build_bundle(settings, with_state=False, with_model=False)
    store = bundle.store

    warm = store.warm_vectors(timeout_s=20.0)
    if warm["warm"] < warm["expected"]:
        raise StoreUnavailableError(
            f"向量索引未就绪：{warm['warm']}/{warm['expected']} 条可召回；"
            "检索不降级为部分结果，请重试或重建索引"
        )
    query = SearchQuery(text=query_from_task(task), limit=args.candidates)
    arms = store.search(query)
    pinned = mentioned_paths(str(task.get("title", "")), str(task.get("symptoms", "")),
                             " ".join(str(item) for item in task.get("keywords", []) or []))
    fused = fuse(arms, pinned_paths=pinned)
    required = [
        (str(item.get("path", "")), str(item.get("symbol", "") or ""))
        for item in task.get("expected_sources") or []
        if item.get("path")
    ]
    fused = ensure_required(fused, arms, required)
    if not fused:
        print("ERROR: 检索结果为空；请先执行 mmctx import", file=sys.stderr)
        return 8

    digest = current_spec_digest(settings)
    stats = store.stats()
    meta = {"table": stats.table, "chunks": stats.chunks, "current": stats.current_chunks,
            "docs": stats.docs, "parser": stats.parser}
    base_commit = bundle.workspace.head()
    pack = build_pack(
        task,
        fused,
        spec_digest=digest,
        budget_tokens=settings.budget_tokens,
        db=meta,
        corpus_stats={"spec": len(spec_items(settings)), "docs": len(corpus_documents(settings))},
        base_commit=base_commit,
    )
    require_verified(pack, read_source_factory(settings, base_commit), store.lookup)

    missing = check_expected_sources(pack, task)
    if missing:
        devlog_write(settings, run_id, {"step": "error", "code": "expected_sources_missing", "missing": missing})
        for item in missing:
            print(f"FAIL 缺少必要来源：{item}", file=sys.stderr)
        return 3

    settings.out_dir.mkdir(parents=True, exist_ok=True)
    pack_path = settings.out_dir / f"{task['task_id']}.json"
    md_path = settings.out_dir / f"{task['task_id']}.md"
    pack_text = json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True)
    pack_path.write_text(pack_text + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(pack), encoding="utf-8")

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    context_sha = hashlib.sha256(pack_text.encode("utf-8")).hexdigest()
    summary = {
        "run_id": run_id,
        "task_id": task["task_id"],
        "spec_digest": digest,
        "base_commit": base_commit,
        "context_sha256": context_sha,
        "vector_warm": warm,
        "sources": len(pack["sources"]),
        "counts": pack["counts"],
        "used_tokens": pack["used_tokens"],
        "statements_in_search": store.statements_in_last_search,
        "latency_ms": elapsed_ms,
        "pack": rel(pack_path, settings.root),
        "pack_md": rel(md_path, settings.root),
    }
    devlog_write(settings, run_id, {"step": "context", **summary})
    print_json(summary)
    return 0


def _apply_workspace_guard(workspace: Any, diff: str) -> tuple[bool, str]:
    paths, errors = guard_patch(diff)
    if errors:
        return False, "; ".join(errors)
    try:
        workspace.apply_patch(diff)
    except Exception as error:  # noqa: BLE001 - 失败原因要回给模型重试
        return False, str(error)
    return True, ""


def _target_tests(settings: Settings) -> tuple[int, str]:
    """应用补丁后、提交前先跑目标测试；失败则回滚并让模型带着测试输出重试。"""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=settings.demo_project,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, (result.stdout + result.stderr).rstrip()


def cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    from mmctx.bootstrap.main import build_bundle

    run_id = args.run_id or new_run_id()
    if not settings.minimax_api_key:
        print("ERROR: 缺少 MINIMAX_API_KEY，无法调用 MiniMax（项②需要真实调用）", file=sys.stderr)
        devlog_write(settings, run_id, {"step": "error", "code": "missing_api_key"})
        return 4

    task = load_task(Path(args.task))
    bundle = build_bundle(settings, with_store=True, with_state=False)
    workspace = bundle.workspace
    model = bundle.model
    status = workspace.status(["demo_project"])
    if status and not args.allow_dirty:
        print("ERROR: 工作区有未提交改动，先提交再运行（或用 --allow-dirty）", file=sys.stderr)
        print(status, file=sys.stderr)
        return 4

    pack = json.loads(Path(args.context).read_text(encoding="utf-8"))
    if str(pack.get("task_id", "")) != str(task["task_id"]):
        print(f"ERROR: 上下文包属于 {pack.get('task_id')!r}，与任务 {task['task_id']!r} 不匹配", file=sys.stderr)
        devlog_write(settings, run_id, {"step": "error", "code": "context_task_mismatch"})
        return 4
    require_verified(
        pack,
        read_source_factory(settings, pack.get("base_commit") or None),
        bundle.store.lookup,
    )
    pack_text = json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True)
    context_sha = hashlib.sha256(pack_text.encode("utf-8")).hexdigest()
    md = render_markdown(pack)

    units = build_units(task, md)
    base_commit = workspace.head()
    prompt_text = "\n\n".join(f"[{unit.role}]\n{unit.text}" for unit in units)
    settings.devlog_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = settings.devlog_dir / f"{run_id}.prompt.txt"
    prompt_path.write_text(prompt_text, encoding="utf-8")
    devlog_write(
        settings,
        run_id,
        {
            "step": "run",
            "task_id": task["task_id"],
            "context": args.context,
            "context_sha256": context_sha,
            "prompt": rel(prompt_path, settings.root),
            "prompt_sha256": sha256_hex(prompt_text, 64),
            "spec_digest": pack.get("spec_digest", ""),
            "base_commit": base_commit,
            "model": settings.minimax_model,
            "base_url": settings.minimax_base_url,
        },
    )
    attempts: list[dict[str, Any]] = []
    last_error = ""
    for attempt in range(1, args.retries + 2):
        try:
            reply = model.complete(ModelRequest(units=units, output_allowance=settings.minimax_max_completion_tokens))
        except ModelError as error:
            devlog_write(
                settings,
                run_id,
                {"step": "error", "code": error.code, "reason": str(error),
                 "attempt": attempt, "context_sha256": context_sha},
            )
            print(f"ERROR: {error}", file=sys.stderr)
            return 4
        raw_path = settings.devlog_dir / f"{run_id}.reply-{attempt}.txt"
        settings.devlog_dir.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(reply.text, encoding="utf-8")

        diff = extract_diff(reply.text)
        diff_source = "reply_diff"
        if not diff:
            candidate = extract_python_file(reply.text)
            if candidate:
                try:
                    compile(candidate, TARGET_FILE, "exec")
                except SyntaxError as error:
                    last_error = f"模型给出的整文件无法通过 Python 语法检查：{error}"
                else:
                    old = (settings.root / TARGET_FILE).read_text(encoding="utf-8")
                    diff = build_file_diff(TARGET_FILE, old, candidate)
                    diff_source = "full_file"
                    if not diff:
                        last_error = "模型给出的整文件与当前文件相同，没有改动"
            else:
                last_error = "模型回复里既没有 diff，也没有完整的 filters.py"

        record = {
            "step": "model",
            "attempt": attempt,
            "model": reply.model,
            "base_url": settings.minimax_base_url,
            "finish_reason": reply.finish_reason,
            "usage": reply.usage,
            "latency_ms": reply.latency_ms,
            "request_id": reply.request_id,
            "reply_sha256": sha256_hex(reply.text, 64),
            "raw_reply": rel(raw_path, settings.root),
            "diff_present": bool(diff),
            "patch_source": diff_source if diff else "",
        }
        attempts.append(record)
        devlog_write(settings, run_id, record)

        ok = False
        if diff:
            ok, last_error = _apply_workspace_guard(workspace, diff)
            patch_path = settings.devlog_dir / f"{run_id}.patch-{attempt}.diff"
            patch_path.write_text(diff, encoding="utf-8")
            devlog_write(
                settings,
                run_id,
                {
                    "step": "patch",
                    "attempt": attempt,
                    "paths": guard_patch(diff)[0],
                    "patch_sha256": sha256_hex(diff, 64),
                    "patch": rel(patch_path, settings.root),
                    "patch_source": diff_source,
                    "applied": ok,
                    "error": last_error,
                },
            )
            if ok:
                test_rc, test_tail = _target_tests(settings)
                devlog_write(
                    settings,
                    run_id,
                    {"step": "self_check", "attempt": attempt, "exit_code": test_rc,
                     "tail": test_tail[-800:]},
                )
                if test_rc != 0:
                    # 能 apply 不等于改对了：回滚，把失败测试回灌给模型，再试。
                    workspace.revert(guard_patch(diff)[0])
                    ok = False
                    last_error = f"补丁能应用但目标测试失败（exit {test_rc}）：\n{test_tail[-1200:]}"
                else:
                    if args.commit:
                        applied_commit = workspace.commit(
                            f"fix(mmctx): implement {task['task_id']} via {settings.minimax_model}",
                            guard_patch(diff)[0],
                        )
                    else:
                        applied_commit = ""
                    devlog_write(
                        settings,
                        run_id,
                        {"step": "apply", "applied_commit": applied_commit, "base_commit": base_commit,
                         "files": guard_patch(diff)[0], "patch_source": diff_source,
                         "self_check_exit_code": test_rc},
                    )
                    print_json(
                        {
                            "run_id": run_id,
                            "task_id": task["task_id"],
                            "model": reply.model,
                            "context_sha256": context_sha,
                            "base_commit": base_commit,
                            "applied_commit": applied_commit,
                            "attempts": len(attempts),
                            "patch_source": diff_source,
                            "files": guard_patch(diff)[0],
                        }
                    )
                    return 0

        previous = reply.text
        if len(previous) > 6000:
            previous = previous[:6000] + "\n...(截断)"
        hint = (
            "请直接输出修改后的完整 demo_project/shop/filters.py，放在一个 ```python 代码块里；"
            "不要输出 diff、解释或测试文件。"
            if diff_source == "full_file" or not diff
            else "请重新输出完整 unified diff；只改 demo_project/shop/filters.py；"
            "hunk 头的行数必须与实际列出的行数完全一致；仍然不许改测试。"
        )
        units = list(units) + [
            PromptUnit(
                role="user",
                text=(
                    f"上一次输出不能应用：{last_error}\n"
                    "上一次输出如下（不要原样重复，只修正错误）：\n"
                    f"{previous}\n{hint}"
                ),
            )
        ]

    devlog_write(settings, run_id, {"step": "error", "code": "patch_failed", "reason": last_error,
                                    "attempts": len(attempts)})
    print(f"ERROR: 模型修改失败（{last_error}）", file=sys.stderr)
    return 4


def cmd_test(args: argparse.Namespace, settings: Settings) -> int:
    run_id = args.run_id or new_run_id()
    from mmctx.adapters.gitrepo import Workspace

    workspace = Workspace(settings.root)
    touched = workspace.status(["demo_project/tests", "demo_project/pytest.ini", "pytest.ini"])
    if touched:
        print("ERROR: 测试文件/测试配置有未提交改动，测试结果不可作为验收证据：", file=sys.stderr)
        print(touched, file=sys.stderr)
        devlog_write(settings, run_id, {"step": "error", "code": "tests_modified", "git_status": touched})
        return 4
    command = [sys.executable, "-m", "pytest", "-q", *args.pytest_args]
    started = time.perf_counter()
    result = subprocess.run(command, cwd=settings.demo_project, capture_output=True, text=True, check=False)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    output = (result.stdout + result.stderr).rstrip()
    head = workspace.head()
    status = workspace.status()
    tests_digest = sha256_hex(
        "\n".join(
            f"{path.relative_to(settings.root).as_posix()}\0{sha256_hex(path.read_text(encoding='utf-8'), 64)}"
            for path in sorted((settings.demo_project / "tests").rglob("*.py"))
        ),
        64,
    )

    log_path = Path(args.log) if args.log else settings.devlog_dir / f"{run_id}.{args.label}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_text = "\n".join(
        [
            "# mmctx test log",
            f"label: {args.label}",
            f"utc: {utc_now()}",
            f"command: {' '.join(command)}",
            f"cwd: {settings.demo_project}",
            f"commit: {head}",
            f"git_status: {status or '<clean>'}",
            f"tests_sha256: {tests_digest}",
            f"exit_code: {result.returncode}",
            "",
            output,
            "",
        ]
    )
    log_path.write_text(log_text, encoding="utf-8")
    devlog_write(
        settings,
        run_id,
        {
            "step": "test",
            "label": args.label,
            "command": " ".join(command),
            "exit_code": result.returncode,
            "commit": head,
            "git_status": status,
            "tests_sha256": tests_digest,
            "elapsed_ms": elapsed_ms,
            "log": rel(log_path, settings.root),
        },
    )
    print(log_text)
    print(f"[mmctx] label={args.label} exit_code={result.returncode} commit={head}")
    return result.returncode


def cmd_state(args: argparse.Namespace, settings: Settings) -> int:
    from mmctx.bootstrap.main import build_bundle

    run_id = args.run_id or new_run_id()
    task = load_task(Path(args.task))
    pack = json.loads(Path(args.context).read_text(encoding="utf-8"))
    bundle = build_bundle(settings, with_store=False, with_model=False)
    state_port = bundle.state

    # 读失败必须显式失败（exit 9），不许静默把 seq 重置为 1 后写坏状态。
    previous = state_port.load_latest(str(task["task_id"]))
    seq = (previous.seq if previous else 0) + 1
    latest_apply = _latest_apply(settings) or {}
    state = build_state(
        task_id=str(task["task_id"]),
        title=str(task.get("title", "")),
        run_id=str(latest_apply.get("run_id") or args.run_id or run_id),
        spec_digest=str(pack.get("spec_digest") or current_spec_digest(settings)),
        context_sha256=hashlib.sha256(
            json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        applied_commit=args.applied_commit or str(latest_apply.get("applied_commit", "")),
        status=args.status,
        todo=args.todo,
        seq=seq,
        updated_at=utc_now(),
    )
    todo = list(state["todo"])
    title = str(state["title"])
    truncated = False
    while True:
        candidate = dict(state, todo=todo, title=title)
        if truncated:
            candidate["truncated"] = True
        text = pack_state(candidate)
        try:
            ref = state_port.save(str(task["task_id"]), text, seq=seq, reason=f"mmctx state {task['task_id']}")
            break
        except StateTooLargeError:
            if todo:
                todo.pop()
                truncated = True
                continue
            if len(title) > 40:
                title = title[:40]
                truncated = True
                continue
            devlog_write(settings, run_id, {"step": "error", "code": "state_too_large",
                                            "state_bytes": len(text.encode("utf-8"))})
            raise
    summary = {
        "task_id": task["task_id"],
        "entry_id": ref.entry_id,
        "version": ref.version,
        "unchanged": ref.unchanged,
        "seq": seq,
        "truncated": truncated,
        "applied_commit": candidate["applied_commit"],
        "run_id_from_state": candidate["run_id"],
        "state_bytes_utf8": len(text.encode("utf-8")),
        "state_sha256": state_hash(candidate),
    }
    devlog_write(settings, run_id, {"step": "state", **summary})
    print_json(summary)
    return 0


def cmd_resume(args: argparse.Namespace, settings: Settings) -> int:
    from mmctx.bootstrap.main import build_bundle

    run_id = args.run_id or new_run_id()
    task = load_task(Path(args.task))
    bundle = build_bundle(settings, with_store=False, with_model=False)
    record = bundle.state.load_latest(str(task["task_id"]))
    if record is None:
        devlog_write(settings, run_id, {"step": "resume", "task_id": task["task_id"], "found": False})
        print(f"ERROR: 没有找到任务 {task['task_id']} 的状态", file=sys.stderr)
        return 6
    state = load_state(record.text)
    digest = current_spec_digest(settings)
    stale = is_stale(state, digest)
    summary = {
        "task_id": task["task_id"],
        "found": True,
        "entry_id": record.ref.entry_id,
        "version": record.ref.version,
        "seq": record.seq,
        "run_id_from_state": state.get("run_id"),
        "todo": state.get("todo", []),
        "applied_commit": state.get("applied_commit"),
        "stale": stale,
        "state_spec_digest": state.get("spec_digest"),
        "current_spec_digest": digest,
        "next_action": "规格已更新，需要重新执行 import 与 context" if stale else "可以继续",
    }
    devlog_write(settings, run_id, {"step": "resume", **summary})
    print_json(summary)
    return 5 if stale else 0


def cmd_verify(args: argparse.Namespace, settings: Settings) -> int:
    run_id = args.run_id or new_run_id()
    errors: list[str] = []
    checked = 0
    if args.context:
        pack = json.loads(Path(args.context).read_text(encoding="utf-8"))
        from mmctx.bootstrap.main import build_bundle

        store = build_bundle(settings, with_state=False, with_model=False).store
        reader = read_source_factory(settings, pack.get("base_commit") or None)
        errors.extend(verify_sources(pack, reader, store.lookup))
        checked = len(pack.get("sources", []))
    if args.run:
        from mmctx.adapters.gitrepo import Workspace

        log_path = settings.devlog_dir / f"{args.run}.jsonl"
        if not log_path.is_file():
            errors.append(f"devlog not found: {log_path}")
        else:
            entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            patches = [entry for entry in entries if entry.get("step") == "patch" and entry.get("applied")]
            applies = [entry for entry in entries if entry.get("step") == "apply"]
            if not patches:
                errors.append(f"{args.run}: no applied patch recorded")
            else:
                patch_entry = patches[-1]
                patch_path = settings.root / patch_entry["patch"]
                diff = patch_path.read_text(encoding="utf-8")
                if sha256_hex(diff, 64) != patch_entry["patch_sha256"]:
                    errors.append(f"{patch_path}: patch sha256 mismatch")
                workspace = Workspace(settings.root)
                ok, message = workspace.reverse_check(diff)
                if not ok:
                    errors.append(f"{patch_path}: patch does not match applied tree ({message})")
                if applies and applies[-1].get("applied_commit"):
                    commit = applies[-1]["applied_commit"]
                    for path in guard_patch(diff)[0]:
                        try:
                            workspace.show(path, commit)
                        except FileNotFoundError as error:
                            errors.append(f"applied commit does not contain patched file: {error}")
                checked += len(guard_patch(diff)[0])
    if not args.context and not args.run:
        print("ERROR: verify 需要 --context 或 --run", file=sys.stderr)
        return 2

    devlog_write(settings, run_id, {"step": "verify", "ok": not errors, "checked": checked, "errors": errors})
    if errors:
        for error in errors:
            print(f"FAIL {error}", file=sys.stderr)
        return 3
    print_json({"ok": True, "checked": checked, "context": args.context, "run": args.run})
    return 0


def cmd_explain(args: argparse.Namespace, settings: Settings) -> int:
    from mmctx.bootstrap.main import build_bundle

    task = load_task(Path(args.task))
    store = build_bundle(settings, with_state=False, with_model=False).store
    lines = store.explain(SearchQuery(text=query_from_task(task), limit=args.candidates))
    text = "\n".join(lines)
    print(text)
    has_fts = "TEXT RETRIEVAL SCAN" in text
    has_vec = "VECTOR INDEX" in text
    print_json({"fulltext_index_used": has_fts, "vector_index_used": has_vec})
    return 0 if (has_fts and has_vec) else 3


def cmd_check(args: argparse.Namespace, settings: Settings) -> int:
    forbidden = [
        "pylibseekdb",
        "SeekdbStore(",
        "PowerContextState(",
        "MiniMaxModel(",
        "import httpx",
        "import pymysql",
    ]
    problems: list[str] = []
    scanned = 0
    for folder in ("ports", "core"):
        for path in sorted((settings.root / "mmctx" / folder).glob("*.py")):
            scanned += 1
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    problems.append(f"{rel(path, settings.root)}: forbidden token {token!r}")
    print_json({"ok": not problems, "files_scanned": scanned, "problems": problems})
    return 0 if not problems else 1


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mmctx", description="MiniMax SDD 上下文管理")
    parser.add_argument("--root", default=None, help="minimax/ 目录（默认取包所在目录）")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="建表并检查 PowerContext / MiniMax 配置")
    init.set_defaults(func=cmd_init)

    imp = sub.add_parser("import", help="扫描并导入语料")
    imp.add_argument("--run-id", default=None)
    imp.add_argument("--reset", action="store_true", help="先清空索引再导入")
    imp.add_argument("--no-pc", action="store_true", help="只写 seekdb，不登记 PowerContext Source")
    imp.set_defaults(func=cmd_import)

    ctx = sub.add_parser("context", help="组装上下文包并反查来源")
    ctx.add_argument("--task", required=True)
    ctx.add_argument("--run-id", default=None)
    ctx.add_argument("--candidates", type=int, default=40)
    ctx.set_defaults(func=cmd_context)

    run = sub.add_parser("run", help="调用 MiniMax 生成并应用补丁")
    run.add_argument("--task", required=True)
    run.add_argument("--context", required=True)
    run.add_argument("--run-id", default=None)
    run.add_argument("--retries", type=int, default=3)
    run.add_argument("--commit", action="store_true")
    run.add_argument("--allow-dirty", action="store_true")
    run.set_defaults(func=cmd_run)

    test = sub.add_parser("test", help="运行目标测试并记录命令/退出码/版本")
    test.add_argument("--label", default="test")
    test.add_argument("--run-id", default=None)
    test.add_argument("--log", default=None)
    test.add_argument("pytest_args", nargs="*", default=[])
    test.set_defaults(func=cmd_test)

    state = sub.add_parser("state", help="保存任务状态到 PowerContext")
    state.add_argument("--task", required=True)
    state.add_argument("--context", required=True)
    state.add_argument("--run-id", default=None)
    state.add_argument("--applied-commit", default=None)
    state.add_argument("--status", default="applied")
    state.add_argument("--todo", action="append", default=[])
    state.set_defaults(func=cmd_state)

    resume = sub.add_parser("resume", help="重启后读回任务状态")
    resume.add_argument("--task", required=True)
    resume.add_argument("--run-id", default=None)
    resume.set_defaults(func=cmd_resume)

    verify = sub.add_parser("verify", help="反查上下文来源 / 重放补丁")
    verify.add_argument("--context", default=None)
    verify.add_argument("--run", default=None)
    verify.add_argument("--run-id", default=None)
    verify.set_defaults(func=cmd_verify)

    explain = sub.add_parser("explain", help="打印候选 SQL 的 EXPLAIN")
    explain.add_argument("--task", required=True)
    explain.add_argument("--candidates", type=int, default=40)
    explain.set_defaults(func=cmd_explain)

    check = sub.add_parser("check", help="分层判据：core/ports 不得 import 适配器/SDK")
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    from mmctx.adapters.pc_state import PCUnavailableError, PCError
    from mmctx.adapters.seekdb_store import StoreUnavailableError
    from mmctx.adapters.gitrepo import GitError

    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else None
    settings = build_settings(root)
    try:
        return int(args.func(args, settings))
    except StoreUnavailableError as error:
        print(f"ERROR(store): {error}", file=sys.stderr)
        return 8
    except PCUnavailableError as error:
        print(f"ERROR(powercontext): {error}", file=sys.stderr)
        return 9
    except PCError as error:
        print(f"ERROR(powercontext): {error}", file=sys.stderr)
        return 9
    except StateTooLargeError as error:
        print(f"ERROR(state): {error}", file=sys.stderr)
        return 7
    except VerifyError as error:
        for item in error.errors:
            print(f"FAIL {item}", file=sys.stderr)
        return 3
    except ModelError as error:
        print(f"ERROR(model): {error}", file=sys.stderr)
        return 4
    except GitError as error:
        print(f"ERROR(git): {error}", file=sys.stderr)
        return 4
    except (ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: 无法解析输入：{error}", file=sys.stderr)
        return 2
    except KeyError as error:
        print(f"ERROR: 缺少字段：{error}", file=sys.stderr)
        return 2
    except FileNotFoundError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
