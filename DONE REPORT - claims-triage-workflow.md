# DONE REPORT - claims-triage-workflow

## Status

Offline implementation and verification are complete on branch
`chore/dense-hybrid-retrieval`. The live embedding/index measurement and hybrid
cassette comparison are pending owner approval before any Gemini calls. The
default remains `RETRIEVER=bm25`; no dense or hybrid gains are claimed. No commit
has been created or branch pushed.

## Steps

| Step | Result |
|---|---|
| 0. Baseline | Completed: environment sync, offline checks, and replay evaluation passed. |
| 1. Retrieval gold set and metrics | Completed for 43 synthetic cases; BM25 metrics recorded. CASE-43 is flagged for owner review because retrieval is skipped for this missing-claim scenario. |
| 2. Embedders | Completed: Gemini, deterministic hash, normalized cache, sanitization, and replay-miss behavior are implemented and covered offline. |
| 3. Vector store | Completed: exact NumPy cosine index and corpus/model manifest validation are implemented and covered offline. |
| 4. Hybrid retrieval | Completed: opt-in dense/hybrid modes, RRF, pinned procedure clause, trace/state/review packet, telemetry, and non-replay fallback are implemented. |
| 5. Offline tests | Completed: offline suite passes; socket connections are blocked in the offline hash-embedder test. |
| 6. Measure and decide | Pending owner approval for live Gemini embedding calls and cassette recording. Dense/hybrid are not measured; BM25 remains default. |
| 7. Documentation | Completed for the offline implementation and measurement status. README tree test added. |

## Commands and results

- `uv sync` - passed during baseline setup.
- `uv run ruff check . && uv run ruff format --check . && uv run pytest -q -m "not integration"` - passed: 162 passed, 1 deselected, 2 warnings.
- `uv run python -m evals.run_eval --mode oracle` - passed; all eight CI gates passed.
- `uv run python -m evals.run_eval --mode replay` - passed; all eight CI gates passed.
- `uv run python -m evals.retrieval_eval --mode bm25` - passed: Recall@1 0.9302, Recall@3 1.0000, Recall@4 1.0000, MRR 1.0000, nDCG@4 1.0000, 43 cases.
- `uv run python scripts/build_index.py --help` - passed; options inspected without network access.
- `git diff --check` - passed.

The initial lint/format/test command stopped at a formatting check in
`evals/retrieval_eval.py`; the file was formatted, then the complete command
above passed. Oracle and replay evaluations were rerun sequentially after an
initial concurrent run to avoid races while writing the shared `latest`
evaluation report.

## Retrieval metrics

| Mode | Recall@1 | Recall@3 | Recall@4 | MRR | nDCG@4 | Cases | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| BM25 | 0.9302 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 43 | Measured |
| Dense (`gemini-embedding-001`) | not measured | not measured | not measured | not measured | not measured | not measured | Requires authorized live embeddings/index |
| Hybrid (`gemini-embedding-001`) | not measured | not measured | not measured | not measured | not measured | not measured | Requires authorized live embeddings/index |

The retrieval gold set uses the expected chunk IDs in each synthetic case's
gold assessment. CASE-43 is the only label marked uncertain: the case has a gold
procedure citation, but the live workflow skips retrieval after the claim is
found missing. Please review whether it should remain included in retrieval
quality metrics.

## Files changed

- Retrieval/evaluation: `evals/retrieval_gold.jsonl`,
  `evals/retrieval_eval.py`, `evals/reports/retrieval.md`,
  `src/triage/retrieval.py`.
- Embeddings/index: `src/triage/embeddings.py`, `src/triage/vectorstore.py`,
  `scripts/build_index.py`, `src/triage/llm.py`, `pyproject.toml`, `uv.lock`.
- Workflow/config/telemetry: `src/triage/config.py`, `src/triage/graph.py`,
  `src/triage/nodes.py`, `src/triage/state.py`, `src/triage/logging_setup.py`,
  `.env.example`.
- Tests: `tests/test_config.py`, `tests/test_dense_retrieval.py`,
  `tests/test_embeddings.py`, `tests/test_logging_telemetry.py`,
  `tests/test_readme_tree.py`, `tests/test_retrieval_eval.py`,
  `tests/test_retrieval_node.py`, `tests/test_vectorstore.py`.
- Documentation: `README.md`, `docs/decisions.md`, `docs/sdk_notes.md`, and this
  report.

## Not completed

- No Gemini `models.list()` request, live embedding calls, cache/index artifact
  generation, hybrid end-to-end cassette recording, or dense/hybrid quality
  measurement was performed.
- No commit or push was performed. The live measurement step remains blocked
  pending owner approval; after that step and final verification, commits and a
  branch-only push can be completed.
