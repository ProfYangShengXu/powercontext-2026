"""Build the deterministic proxy question set for the PDF evidence-page task.

The set is generated *only* from the public PepsiCo 2022 10-K PDF that
``make -C competition test-pdf`` uses.  No external model is involved: every
question is derived by a fixed template from text that is already on the target
page, so the output is byte-stable for a given PDF and seed.

Three construction families are mixed:

``numeric``
    A line of the form ``<label> $ <number>`` on a financial table becomes
    ``What was <label> for PepsiCo in fiscal 2022?``.  The number itself is not
    put in the question (the question asks for it), so the page has to be found
    from the label and period.
``entity``
    A section heading (``Item 1A. Risk Factors``, ``Consolidated Statement of
    Income``, ``Note 3 ...``) becomes a "what is covered under ..." question.
``first-sentence``
    The first substantive sentence of a page is reduced to its content-token
    phrase and asked about.

Usage::

    python -m tools.pdf_recall_eval.build_dataset \
        --pdf competition/challenges/source-03-pdf-retrieval/workload/public/pdfs/PEPSICO_2022_10K.pdf \
        --out tools/pdf_recall_eval/proxy_dataset.jsonl

Run this from the repository root so ``src/`` and ``tools/`` are importable
(``PYTHONPATH=src:.``).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

from powercontext.sources.pdf_pages import (
    PDFDocumentInput,
    _QUERY_STOPWORDS,
    parse_pdf_document,
)

DEFAULT_PDF = (
    "competition/challenges/source-03-pdf-retrieval/workload/public/pdfs/PEPSICO_2022_10K.pdf"
)
DOCUMENT_ID = "PEPSICO_2022_10K"
SEED = 20221031
TARGET_PER_FAMILY = 34

_WS = re.compile(r"\s+")
_TOKEN = re.compile(r"[A-Za-z]+|\d+")
# A financial-table row anchored at the start of a physical line, e.g.
#   "Net Revenue $ 86,392 $ 79,474 $ 70,372"  or  "Total $ 411 $ 247 $ 289"
_NUMERIC_ROW = re.compile(
    r"^(?P<label>[A-Z][A-Za-z][A-Za-z &,'()./-]{2,55}?)\s+\$\s*(?P<number>\d[\d,]{2,})"
)
# A table row without a currency sign, e.g. "Cost of sales 40,576 37,075 31,797".
# Only applied on pages already proven to be table pages by _NUMERIC_ROW.
_NUMERIC_PLAIN_ROW = re.compile(
    r"^(?P<label>[A-Z][A-Za-z][A-Za-z &,'()./-]{2,45}?)\s+(?P<number>\d[\d,]{2,})\s+\d"
)
# Section headings that actually start a section, not a table-of-contents entry
# (TOC entries end in a bare page number and are filtered separately).
_HEADING = re.compile(
    r"^(?:Item\s+\d+[A-Z]?\.[ \t]+[A-Za-z][^\n]{3,70}"
    r"|Consolidated\s+(?:Statements?|Balance\s+Sheets?)\s+of\s+[A-Za-z][^\n]{2,60}"
    r"|Consolidated\s+Balance\s+Sheets?"
    r"|Note\s+\d+[ \t]+[A-Z][^\n]{2,60}"
    r"|Notes\s+to\s+Consolidated\s+Financial\s+Statements"
    r"|PART\s+[IVX]+)$"
)
_TOC_LINE = re.compile(r"^Item\s+\d+[A-Z]?\..*\s+\d{1,3}$")
_BAD_LABEL_TAIL = frozenset(
    "a an the and or of to in on at by for from with as is are was were be been "
    "we our its it this that these those".split()
)


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN.findall(text)]


def _content_tokens(text: str) -> list[str]:
    return [
        token.lower()
        for token in _TOKEN.findall(text)
        if token.lower() not in _QUERY_STOPWORDS and (len(token) > 1 or token.isdigit())
    ]


def _sentences(text: str) -> list[str]:
    cleaned = _WS.sub(" ", text.replace("\n", " ")).strip()
    cleaned = re.sub(r"^Table of Contents\s*", "", cleaned, flags=re.IGNORECASE)
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]


def _page_texts(pdf: Path) -> list[str]:
    pages = parse_pdf_document(PDFDocumentInput(document_id=DOCUMENT_ID, content=pdf.read_bytes()))
    return [page.text for page in pages]


def _numeric_candidates(page_texts: list[str], limit: int) -> list[dict]:
    # A label is only usable when it names exactly one page: otherwise the gold
    # page would be ambiguous.  Rows must be line-anchored table rows, so prose
    # that happens to contain a number is rejected; a page must also carry at
    # least three such rows to count as a table page.
    rows_by_page: dict[int, list[tuple[str, str]]] = {}
    for number, text in enumerate(page_texts, start=1):
        rows: list[tuple[str, str]] = []
        plain: list[tuple[str, str]] = []
        for line in text.splitlines():
            match = _NUMERIC_ROW.match(line.strip())
            if match is not None:
                label = match.group("label").strip()
                words = label.split()
                if len(words) < 2 or words[-1].casefold() in _BAD_LABEL_TAIL:
                    continue
                content = _content_tokens(label)
                if len(content) < 2 or not any(len(token) > 3 for token in content):
                    continue
                if not any(character.isalpha() for character in label):
                    continue
                rows.append((label, match.group("number")))
                continue
            plain_match = _NUMERIC_PLAIN_ROW.match(line.strip())
            if plain_match is not None:
                label = plain_match.group("label").strip()
                words = label.split()
                if len(words) < 2 or words[-1].casefold() in _BAD_LABEL_TAIL:
                    continue
                if any(character.isdigit() for character in label):
                    continue
                plain.append((label, plain_match.group("number")))
        if len(rows) >= 2:
            # A page with currency rows is a table page; its unsigned rows are
            # table rows too, not prose.
            rows_by_page[number] = rows + plain

    label_pages: dict[str, set[int]] = defaultdict(set)
    for number, rows in rows_by_page.items():
        for label, _value in rows:
            label_pages[label.casefold()].add(number)

    seen: set[str] = set()
    out: list[dict] = []
    for number in sorted(rows_by_page):
        for label, value in rows_by_page[number]:
            key = label.casefold()
            pages = sorted(label_pages[key])
            if len(value) < 4 or not 1 <= len(pages) <= 3 or key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "document_id": DOCUMENT_ID,
                    "gold_pages": pages,
                    "construction": "numeric",
                    "anchor": label,
                    "question": (
                        f"What was {label} reported as in PepsiCo's fiscal 2022 consolidated statements?"
                    ),
                }
            )
    return out[:limit]


def _entity_candidates(page_texts: list[str], limit: int) -> list[dict]:
    # Keep headings that appear on exactly one non-TOC page so the target page is
    # unambiguous.  Table-of-contents pages repeat every heading, so they are
    # excluded before uniqueness is computed.
    headings_by_page: list[list[str]] = []
    for text in page_texts:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        toc_like = sum(1 for line in lines if _TOC_LINE.match(line)) >= 5
        headings = [] if toc_like else [line for line in lines if _HEADING.match(line)]
        headings_by_page.append(headings)

    heading_pages: dict[str, list[int]] = defaultdict(list)
    heading_text: dict[str, str] = {}
    for number, headings in enumerate(headings_by_page, start=1):
        for heading in headings:
            heading = re.sub(r"\s+\d{1,3}$", "", heading).strip(" .")
            key = heading.casefold()
            heading_pages[key].append(number)
            heading_text.setdefault(key, heading)

    out: list[dict] = []
    for key, pages in heading_pages.items():
        unique_pages = sorted(set(pages))
        if not 1 <= len(unique_pages) <= 3:
            continue
        heading = heading_text[key]
        if len(_content_tokens(heading)) < 2:
            continue
        out.append(
            {
                "document_id": DOCUMENT_ID,
                "gold_pages": unique_pages,
                "construction": "entity",
                "anchor": heading,
                "question": (
                    f"According to PepsiCo's FY2022 Form 10-K, what is covered under "
                    f'"{heading}"?'
                ),
            }
        )
    return out[:limit]


def _first_sentence_candidates(page_texts: list[str], limit: int) -> list[dict]:
    out: list[dict] = []
    for number, text in enumerate(page_texts, start=1):
        sentences = _sentences(text)
        if not sentences:
            continue
        sentence = next((item for item in sentences if len(_content_tokens(item)) >= 4), "")
        content = list(dict.fromkeys(_content_tokens(sentence)))[:7]
        if len(content) < 4:
            continue
        phrase = " ".join(content)
        out.append(
            {
                "document_id": DOCUMENT_ID,
                "gold_pages": [number],
                "construction": "first-sentence",
                "anchor": phrase,
                "question": (
                    f"According to PepsiCo's FY2022 annual report, what does the filing "
                    f"state about {phrase}?"
                ),
            }
        )
    return out[:limit]


def build(pdf: Path, seed: int = SEED, target: int = TARGET_PER_FAMILY, limit: int = 100) -> list[dict]:
    page_texts = _page_texts(pdf)
    rng = random.Random(seed)
    families = [
        ("numeric", _numeric_candidates),
        ("entity", _entity_candidates),
        ("first-sentence", _first_sentence_candidates),
    ]
    by_family = {name: factory(page_texts, len(page_texts)) for name, factory in families}
    for candidates in by_family.values():
        rng.shuffle(candidates)

    selected: list[dict] = []
    page_uses: dict[int, int] = defaultdict(int)
    chosen: set[int] = set()
    max_uses = 2

    def take(family: str, count: int) -> None:
        picked = 0
        for candidate in by_family[family]:
            if id(candidate) in chosen:
                continue
            pages = candidate["gold_pages"]
            if any(page_uses[page] >= max_uses for page in pages):
                continue
            for page in pages:
                page_uses[page] += 1
            chosen.add(id(candidate))
            selected.append(candidate)
            picked += 1
            if picked >= count:
                return

    # Every family gets a real share; whatever the two narrow families cannot
    # supply (they are limited by unique pages) is filled from the broad one.
    take("numeric", target)
    take("entity", target)
    take("first-sentence", target)
    if len(selected) < limit:
        take("first-sentence", limit - len(selected))
    selected = selected[:limit]
    for index, item in enumerate(selected, start=1):
        item["id"] = f"proxy-{index:04d}"
    selected.sort(key=lambda item: (item["construction"], item["gold_pages"][0]))
    for index, item in enumerate(selected, start=1):
        item["id"] = f"proxy-{index:04d}"
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", default=DEFAULT_PDF)
    parser.add_argument("--out", default="tools/pdf_recall_eval/proxy_dataset.jsonl")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)

    rows = build(Path(args.pdf), seed=args.seed, limit=args.limit)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row["construction"]] += 1
    print(json.dumps({"total": len(rows), "by_construction": dict(counts)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
