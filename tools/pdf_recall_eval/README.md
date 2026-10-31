# PDF evidence-page recall evaluation

Developer tooling for the `source-03-pdf-retrieval` challenge (题四). The grader
loads only `src/`, so nothing here is used at grading time. It exists because the
published acceptance suite checks citation *shape* but never measures the hidden
**evidence-page Recall@20 ≥ 0.70** gate, which made every ranking change a guess.

## What is here

| File | Purpose |
| --- | --- |
| `build_dataset.py` | Generates `proxy_dataset.jsonl` from the public PepsiCo 2022 10-K with three deterministic templates. No model, no network, stable output for a given PDF + seed. |
| `proxy_dataset.jsonl` | 100 generated `(question → gold page(s))` pairs (100 as generated; 27 numeric, 17 entity, 56 first-sentence). |
| `real_questions.jsonl` | The five real FinanceBench questions shipped in the public workload, with page numbers derived from the upstream `evidence_page_num` field (`+1`, since upstream is 0-based). Attribution: FinanceBench revision `cc39aeb4`, CC BY-NC 4.0. |
| `evaluate.py` | Persists a PDF into a real Runtime scope (cached), runs the production `search_pdf_pages`, and reports Recall@20 / Recall@5 / MRR. `--explain` shows each arm's rank for the gold page and an A/B/C classification. |

## Usage

From the repository root:

```bash
# generate / regenerate the proxy set
PYTHONPATH=src:. python -m tools.pdf_recall_eval.build_dataset

# proxy set
PYTHONPATH=src:. python -m tools.pdf_recall_eval.evaluate \
    --dataset tools/pdf_recall_eval/proxy_dataset.jsonl

# the real public questions
PYTHONPATH=src:. python -m tools.pdf_recall_eval.evaluate \
    --dataset tools/pdf_recall_eval/real_questions.jsonl --explain
```

The first run parses and persists the 503-page PDF (~40 s); later runs reuse the
SQLite scope under `$XDG_CACHE_HOME/powercontext/pdf_recall_eval/<doc>-<sha>/`.

`--pdf` / `--document-id` let the same tool measure another FinanceBench filing
(e.g. one of the other four documents in the hidden set) without touching the
repository.

## Metrics

- **Recall@20** — the challenge gate. For each question, `|gold ∩ top20| / |gold|`,
  macro-averaged over questions.
- **Recall@5** — head quality.
- **MRR** — reciprocal rank of the first gold page.

## Explain classification

For each gold page, `--explain` prints the rank under the offline BM25 arm
(`local_rank`), the public-search arm (`search_rank`), the fused rank
(`fused_rank`), and the per-distilled-query rank. It then labels the miss:

- **A** — never entered any candidate pool (recall layer problem: tokenization,
  stemming, vocabulary).
- **B** — entered a pool but the fused rank is beyond the cut-off (fusion / ranking).
- **C** — recalled inside the cut-off, but non-gold pages outrank it (precision /
  reranking).
