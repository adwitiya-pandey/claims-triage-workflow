# DONE REPORT - claims-triage-workflow

Date: 2026-11-01

## Status

Completed the claims-triage hardening task on `chore/claims-hardening`. The branch is pushed to `origin/chore/claims-hardening`; `main` was not changed or merged.

## Steps completed

1. **Baseline:** `uv sync`, lint, format, offline tests, and replay evaluation passed before edits.
2. **Runtime telemetry:** logging is initialized from both `__main__.main()` and `api.create_app()`. Node telemetry is flat, allowlisted JSON with model/token/cost and guardrail fields. Runtime tests cover clean and injection claims.
3. **Execution budgets:** configurable graph recursion, wall-clock, and LLM-call limits fail closed to a persisted `ESCALATE` decision and emit `budget_exhausted`.
4. **README and ignore rules:** corrected auth and port instructions, documented offline setup and guardrails, refreshed the repository tree and added its path-existence test, updated measured output and a real telemetry sample, and ignored SQLite WAL/SHM files. The Mermaid diagram was verified in the GitHub preview after correcting its subgraph identifier.
5. **Final verification:** repeated the baseline checks, started the API, ran the README curl sequence, confirmed successful review resume and stdout telemetry, then pushed the task branch.

## Commands and verification results

| Check | Result |
|---|---|
| `uv sync` | PASS; resolved 74 packages and checked 71 |
| `uv run ruff check .` | PASS on all eight full check runs |
| `uv run ruff format --check .` | PASS on all eight full check runs; 61 files formatted in the latest run |
| `uv run pytest -q -m "not integration"` | PASS on all eight full check runs; 152 passed, 1 integration test deselected, 0 failures per run |
| `uv run python -m evals.run_eval --mode replay` | PASS on all eight full check runs; all configured gates passed |
| Focused logging/budget/config/README tests | PASS; 14 passed |
| API command and README curl examples | PASS on final run; health, four triage requests, pending-review listing, and review resume returned expected results |
| API stdout telemetry | PASS; 30 JSON node events observed in the final curl run, including all expected workflow node names |
| GitHub Mermaid preview | PASS after fix; the final branch preview rendered the diagram |
| Branch push | PASS; `origin/chore/claims-hardening` updated, no merge or push to `main` |

The test runs reported two existing deprecation warnings (Google GenAI typing and Starlette's 422 status constant); neither caused a failure.

## Files changed

- `.env.example`
- `.gitignore`
- `README.md`
- `src/triage/__main__.py`
- `src/triage/api.py`
- `src/triage/config.py`
- `src/triage/graph.py`
- `src/triage/llm.py`
- `src/triage/logging_setup.py`
- `src/triage/nodes.py`
- `src/triage/service.py`
- `tests/test_config.py`
- `tests/test_execution_budget.py`
- `tests/test_logging_runtime.py`
- `tests/test_logging_telemetry.py`
- `tests/test_readme_tree.py`
- `DONE REPORT - claims-triage-workflow.md`

## Skipped or unverified

- The MCP integration test was deselected by the required `-m "not integration"` selector; Project A was not started for this task.
- Live Gemini calls were not made. Unit tests and replay evaluation remained offline.
- PowerShell equivalents are included in the README but could not be executed because neither `pwsh` nor `powershell` is installed in this Linux environment.
- The first API health probe raced server startup and failed to connect; the retry returned HTTP 200 and all subsequent curl requests passed.
- The first GitHub Mermaid preview reported a syntax error; the subgraph identifier was fixed, the branch was pushed again, and the final preview rendered successfully.

## Owner review

Please review the branch diff and the README's PowerShell examples, which remain unverified on an actual PowerShell runtime. No further design decision is needed for this task.
