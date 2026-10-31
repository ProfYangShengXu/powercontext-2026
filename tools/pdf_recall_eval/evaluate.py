"""Measure and explain evidence-page retrieval for a PDF-backed Memory scope.

Two things this tool answers that the published acceptance suite cannot:

1. **Recall / MRR.**  Given a JSONL of ``(question, gold_pages)`` pairs, persist the
   PDF pages into a real Runtime scope once, then run the production
   :func:`powercontext.sources.pdf_pages.search_pdf_pages` and report Recall@20,
   Recall@5 and MRR.  ``--explain`` additionally prints, per question, the rank each
   retrieval arm gave the gold page and an A/B/C classification.

2. **Why a page was missed.**  ``--explain`` calls the same private helpers the
   production function uses (``_list_entries`` -> ``_rank_entries`` ->
   ``_search_stage`` -> ``_fuse``) so the report reflects the shipped code path, not
   a re-implementation.

The index is cached under ``--cache-dir`` (default ``$XDG_CACHE_HOME`` or
``~/.cache``) keyed by the PDF's SHA-256, so repeated runs skip the ~40 s persist.

Usage::

    PYTHONPATH=src:. python -m tools.pdf_recall_eval.evaluate --explain
    PYTHONPATH=src:. python -m tools.pdf_recall_eval.evaluate \
        --dataset tools/pdf_recall_eval/real_questions.jsonl --json

Dataset row format::

    {"id": "...", "document_id": "...", "question": "...", "gold_pages": [62, 64]}
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SearchMemoryRequest,
    SubmitSourceObservation,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import (
    SourceDefinitionRegistry,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources import pdf_pages as pdf_module
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    _distilled_queries,
    _fuse,
    _list_entries,
    _rank_entries,
    _search_stage,
    parse_pdf_document,
    search_pdf_pages,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY

DEFAULT_PDF = (
    "competition/challenges/source-03-pdf-retrieval/workload/public/pdfs/PEPSICO_2022_10K.pdf"
)
DEFAULT_DATASET = "tools/pdf_recall_eval/proxy_dataset.jsonl"
POOL_DEPTH = 100


class _EvidencePipeline:
    """The same one-entry-per-page projection the acceptance harness uses."""

    async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="source-evidence",
                text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"],
                sources=(source,),
            )
            for source in request.sources
            if source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"].strip()
        )


def _cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "powercontext" / "pdf_recall_eval"


async def _persist(directory: Path, observations) -> tuple[BuiltinConfig, str]:
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{directory / 'sources.db'}"))
    async with open_builtin_runtime(config) as runtime:
        scope = await runtime.scopes.create(
            ScopeDraft(title="Sources", summary="Evidence", idempotency_key="sources")
        )
        await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
        for observation in observations:
            await runtime.ingestion.submit(
                SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
            )
        await runtime.ingestion.submit(
            SubmitSourceObservation(scope_id=scope.scope_id, observation=observations[0])
        )
    async with open_builtin_runtime(config, candidate_pipeline=_EvidencePipeline()) as runtime:
        memory = runtime.memory.for_scope(scope.scope_id)
        for _ in range(len(observations) + 1):
            if not (await memory.flush()).processed:
                break
        else:
            raise AssertionError("flush did not drain the captured sources")
    return config, scope.scope_id


async def _ensure_index(pdf: Path, document_id: str, cache_root: Path):
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()[:16]
    directory = cache_root / f"{document_id}-{digest}"
    sidecar = directory / "scope.json"
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{directory / 'sources.db'}"))
    if sidecar.exists():
        return config, json.loads(sidecar.read_text(encoding="utf-8"))["scope_id"]

    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)
    pages = parse_pdf_document(PDFDocumentInput(document_id=document_id, content=pdf.read_bytes()))
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    observations = []
    for page in pages:
        source = await registry.resolve(page)
        observations.append(project_source_for_transport(registry, source))
    config, scope_id = await _persist(directory, observations)
    sidecar.write_text(json.dumps({"scope_id": scope_id}), encoding="utf-8")
    return config, scope_id


async def _source_pages(config: BuiltinConfig, scope_id: str) -> dict[str, int]:
    async with open_builtin_contexts(config) as contexts:
        journal = await (await contexts.get(scope_id)).sources.journal.entries()
    return {
        entry.source_ref.source_id: entry.source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["metadata"][
            "page_number"
        ]
        for entry in journal
    }


async def _citation_pages(memory, citations, source_pages: dict[str, int]) -> list[int]:
    pages: list[int] = []
    for citation in citations:
        entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        for reference in entry.sources:
            pages.append(source_pages[reference.source_id])
    return pages


async def _entry_pages(memory, entries, source_pages) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for record in entries:
        sid = record.entry.sources[0].source_id
        mapping[record.entry.entry_id] = source_pages[sid]
    return mapping


def _rank_of(pages: list[int], page: int) -> int | None:
    for index, value in enumerate(pages, start=1):
        if value == page:
            return index
    return None


async def _explain(memory, question: str, gold: set[int], source_pages, limit: int) -> dict:
    memory_ref, entries = await _list_entries(memory)
    entry_pages = await _entry_pages(memory, entries, source_pages)
    ranked = _rank_entries(memory_ref, entries, question) if entries else []
    searched = await _search_stage(memory, question, limit)
    depth = max(limit, POOL_DEPTH)
    fused = _fuse(ranked, searched, depth)

    local_pages = [entry_pages[item.citation.entry_id] for item in ranked]
    search_pages = [entry_pages[citation.entry_id] for citation in searched]
    fused_pages = [entry_pages[citation.entry_id] for citation in fused]

    mode = "fts"
    hook = getattr(pdf_module, "_preferred_search_mode", None)
    if hook is not None:
        mode = await hook(memory)
    per_query: dict[str, list[int] | None] = {}
    for query in _distilled_queries(question):
        try:
            page = await memory.search(SearchMemoryRequest(query=query, mode=mode, limit=depth))
        except Exception:  # noqa: BLE001 - report a failed arm instead of hiding it.
            per_query[query] = None
            continue
        hits = []
        for hit in page.hits:
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
            hits.append(entry_pages[entry.entry_id])
        per_query[query] = hits

    rows = []
    for page in sorted(gold):
        local_rank = _rank_of(local_pages, page)
        search_rank = _rank_of(search_pages, page)
        fused_rank = _rank_of(fused_pages, page)
        if local_rank is None or local_rank > POOL_DEPTH:
            category = "A" if search_rank is None else "B"
        elif fused_rank is None or fused_rank > limit:
            category = "B"
        elif fused_rank > 1:
            category = "C"
        else:
            category = "ok"
        rows.append(
            {
                "page": page,
                "local_rank": local_rank,
                "search_rank": search_rank,
                "fused_rank": fused_rank,
                "per_query_rank": {
                    query: (_rank_of(pages, page) if pages is not None else None)
                    for query, pages in per_query.items()
                },
                "category": category,
            }
        )
    return {"rows": rows, "local_size": len(local_pages), "search_size": len(search_pages)}


async def run(args) -> dict:
    pdf = Path(args.pdf)
    document_id = args.document_id
    dataset_paths = [Path(item) for item in args.dataset]
    rows = []
    for path in dataset_paths:
        with path.open(encoding="utf-8") as handle:
            rows.extend(json.loads(line) for line in handle if line.strip())
    rows = [row for row in rows if row.get("document_id", document_id) == document_id]
    if not rows:
        raise SystemExit(f"no dataset rows for document_id={document_id!r}")

    cache_root = Path(args.cache_dir) if args.cache_dir else _cache_root()
    config, scope_id = await _ensure_index(pdf, document_id, cache_root)
    source_pages = await _source_pages(config, scope_id)

    recall = {20: 0.0, 5: 0.0}
    reciprocal_rank = 0.0
    categories = {"A": 0, "B": 0, "C": 0, "ok": 0}
    details = []
    async with open_builtin_runtime(config) as runtime:
        memory = runtime.memory.for_scope(scope_id)
        for row in rows:
            gold = set(row["gold_pages"])
            citations = await search_pdf_pages(memory, row["question"], limit=args.limit)
            pages = await _citation_pages(memory, citations, source_pages)
            hit = gold & set(pages)
            for k in recall:
                recall[k] += len(gold & set(pages[:k])) / len(gold)
            first = _rank_of(pages, next(iter(hit))) if hit else None
            reciprocal_rank += 1.0 / first if first else 0.0
            detail = {"id": row["id"], "question": row["question"], "gold_pages": sorted(gold), "got": pages}
            if args.explain:
                explanation = await _explain(memory, row["question"], gold, source_pages, args.limit)
                detail["explain"] = explanation
                for item in explanation["rows"]:
                    categories[item["category"]] += 1
            details.append(detail)

    total = len(rows)
    summary = {
        "document_id": document_id,
        "questions": total,
        "recall@20": round(recall[20] / total, 4),
        "recall@5": round(recall[5] / total, 4),
        "mrr": round(reciprocal_rank / total, 4),
        "categories": categories if args.explain else None,
    }
    if args.explain:
        for detail in details:
            print(f"\n[{detail['id']}] {detail['question'][:110]}")
            print(f"  gold={detail['gold_pages']} got_top20={detail['got'][:20]}")
            for item in detail["explain"]["rows"]:
                print(
                    f"  page {item['page']}: local_rank={item['local_rank']} "
                    f"search_rank={item['search_rank']} fused_rank={item['fused_rank']} "
                    f"category={item['category']} per_query={item['per_query_rank']}"
                )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if args.json:
        print(json.dumps(details, ensure_ascii=False, sort_keys=True))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", default=DEFAULT_PDF)
    parser.add_argument("--document-id", default="PEPSICO_2022_10K")
    parser.add_argument("--dataset", nargs="+", default=[DEFAULT_DATASET])
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--explain", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
