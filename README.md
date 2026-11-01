# Claims Triage Workflow

[![CI](https://github.com/adwitiya-pandey/claims-triage-workflow/actions/workflows/ci.yml/badge.svg)](https://github.com/adwitiya-pandey/claims-triage-workflow/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/badge/managed%20by-uv-orange.svg)](https://github.com/astral-sh/uv)

Meridian Health TPA (fictional) needs an auditable first-pass route for synthetic medical claims. This workflow uses Gemini only to extract fields and label policy clauses; deterministic Python rules choose `FAST_TRACK`, `STANDARD_REVIEW`, or `ESCALATE`. Claims that are not fast-tracked pause for human review, and all claim-system data is read through the separate [`claims-mcp-server`](https://github.com/adwitiya-pandey/claims-mcp-server) repo.

## Architecture

The workflow is linear except for the deterministic route gate: only `FAST_TRACK` bypasses `review`. The MCP server is a read-only boundary, the graph uses a SQLite checkpointer, policy clauses are ranked with the in-repo BM25 retriever, and every node emits allowlisted JSON telemetry.

```mermaid
flowchart LR
    subgraph graph["LangGraph workflow"]
        START([START]) --> intake[intake]
        intake --> extract[extract]
        extract --> fetch_facts[fetch_facts]
        fetch_facts --> retrieve[retrieve]
        retrieve --> assess[assess]
        assess --> decide{decide: deterministic rules}
        decide -->|route == FAST_TRACK| finalize[finalize]
        decide -->|route != FAST_TRACK| review[review: interrupt]
        review --> finalize
        finalize --> END([END])
    end

    fetch_facts <-->|read-only tools| mcp[(claims-mcp-server)]
    retrieve -.-> bm25[BM25 policy retriever]
    review -. pause and resume .-> checkpoint[(SQLite graph checkpointer)]
    finalize -. append-only record .-> decisions[(SQLite decision store)]
    graph -. one flat JSON event per node .-> telemetry[(JSON telemetry sink: stdout)]
```

The graph edges are implemented in [`build_graph`](src/triage/graph.py); the conditional edge sends exactly `FAST_TRACK` to `finalize`, and every other route to `review`.

## Offline by default

The checked-in [`.env.example`](.env.example) sets `LLM_MODE=replay` and `CLAIMS_TOOLS_BACKEND=fixture`. Replay uses the committed response cassette and fixture data, so offline checks and the API need neither a Gemini key nor a running MCP server. For live Gemini calls, set `LLM_MODE=live` and provide `GEMINI_API_KEY`; for claim data from the `claims-mcp-server` repo, set `CLAIMS_TOOLS_BACKEND=mcp`, `CLAIMS_MCP_URL`, and `CLAIMS_MCP_KEY`. Keep credentials in the ignored local `.env`, never in source control.

## Quickstart

Run these commands from the repository root.

```bash
cp .env.example .env
```

Expected output: no output when the copy succeeds.

```bash
uv sync
```

Expected output (trimmed from this session):

```text
Resolved 74 packages in 147ms
Checked 71 packages in 211ms
```

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest -q -m "not integration" && uv run python -m evals.run_eval --mode replay
```

Expected output (trimmed from this session):

```text
All checks passed!
60 files already formatted
152 passed, 1 deselected, 2 warnings in 30.71s
Claims Triage Evaluation Summary (Mode: replay)
M1_false_fast_track_rate         : 0.0000 [PASS]
M2_route_accuracy                : 1.0000 [PASS]
M3_over_escalation_rate          : 0.0000 [INFO]
M4_injection_containment         : 1.0000 [PASS]
M5_citation_validity             : 1.0000 [PASS]
M6_rule_id_match                 : 1.0000 [PASS]
M7_extraction_field_accuracy     : 1.0000 [INFO]
M8_reason_coverage               : 1.0000 [PASS]
ALL CI GATES PASSED
```

### Start the API

The server reads its port from `API_PORT`; command-line `--port` is not supported.

```bash
API_PORT=8800 uv run python -m triage
```

Expected output (trimmed from the API run in this session):

```text
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8800 (Press CTRL+C to quit)
```

#### PowerShell equivalents

The PowerShell executable was not available in the Linux validation environment, so these equivalents are provided but were not executed here.

```powershell
Copy-Item .env.example .env
$env:API_PORT = "8800"
uv run python -m triage
```

Expected output is not verified: PowerShell is not installed in the Linux validation environment. `Copy-Item` should be silent on success; Uvicorn should report `http://127.0.0.1:8800`.

### Call the API

The API uses `X-API-Key` (the dummy local value below comes from `.env.example`). `/health` is public. These calls demonstrate a fast track, a standard-review pause, an escalation pause, an injection escalation, the pending-review list, and a review resume.

```bash
curl -s http://127.0.0.1:8800/health
curl -s -X POST http://127.0.0.1:8800/triage \
  -H "X-API-Key: dummy_triage_api_key" \
  -H "Content-Type: application/json" \
  -d '{"claim_id": "CLM70000001"}'
curl -s -X POST http://127.0.0.1:8800/triage \
  -H "X-API-Key: dummy_triage_api_key" \
  -H "Content-Type: application/json" \
  -d '{"claim_id": "CLM70000009"}'
curl -s -X POST http://127.0.0.1:8800/triage \
  -H "X-API-Key: dummy_triage_api_key" \
  -H "Content-Type: application/json" \
  -d '{"claim_id": "CLM70000020"}'
curl -s -X POST http://127.0.0.1:8800/triage \
  -H "X-API-Key: dummy_triage_api_key" \
  -H "Content-Type: application/json" \
  -d '{"claim_id": "CLM70000034"}'
curl -s http://127.0.0.1:8800/reviews/pending \
  -H "X-API-Key: dummy_triage_api_key"
curl -s -X POST http://127.0.0.1:8800/reviews/triage-CLM70000009-v1 \
  -H "X-API-Key: dummy_triage_api_key" \
  -H "Content-Type: application/json" \
  -d '{"reviewer_id":"rev10001","action":"CONFIRM","reason":"Confirmed standard review after checking the synthetic claim facts."}'
```

Expected output (trimmed from the API run in this session):

```text
{"status":"ok","version":"0.1.0"}
{"status":"completed","decision":{"route_recommended":"FAST_TRACK","route_final":"FAST_TRACK",...}}
{"status":"awaiting_review","thread_id":"triage-CLM70000009-v1","packet":{"recommended_route":"STANDARD_REVIEW",...}}
{"status":"awaiting_review","thread_id":"triage-CLM70000020-v1","packet":{"recommended_route":"ESCALATE",...}}
{"status":"awaiting_review","thread_id":"triage-CLM70000034-v1","packet":{"recommended_route":"ESCALATE",...}}
[{...,"claim_id":"CLM70000009",...},{...}]
{"status":"completed","decision":{"route_recommended":"STANDARD_REVIEW","route_final":"STANDARD_REVIEW",...}}
```

## 2. Repository Structure

Paths in this tree are tracked by Git; their one-line descriptions identify the file's purpose.

```text
claims-triage-workflow/
├── .env.example                 # Offline-friendly configuration template
├── .gitattributes               # Repository text and line-ending rules
├── .github/workflows/ci.yml     # Lint, test, and evaluation CI
├── .gitignore                   # Ignore secrets and local runtime data
├── .pre-commit-config.yaml      # Optional local quality hooks
├── .python-version              # Project Python version
├── .vscode/settings.json        # Workspace editor settings
├── AGENTS.md                    # Repository-specific engineering constraints
├── LICENSE                      # Project license
├── README.md                    # Project guide and operational quickstart
├── SPEC.md                      # Product, safety, and data contract
├── TASKS.md                     # Original project task roadmap
├── THREAT_MODEL.md              # Security threats and mitigations
├── data/
│   ├── cases/
│   │   ├── case-01.json         # Synthetic clean case
│   │   ├── case-02.json         # Synthetic clean case
│   │   ├── case-03.json         # Synthetic clean case
│   │   ├── case-04.json         # Synthetic clean case
│   │   ├── case-05.json         # Synthetic clean case
│   │   ├── case-06.json         # Synthetic clean case
│   │   ├── case-07.json         # Synthetic clean case
│   │   ├── case-08.json         # Synthetic clean case
│   │   ├── case-09.json         # Synthetic preauthorisation case
│   │   ├── case-10.json         # Synthetic preauthorisation case
│   │   ├── case-11.json         # Synthetic preauthorisation case
│   │   ├── case-12.json         # Synthetic coverage case
│   │   ├── case-13.json         # Synthetic coverage case
│   │   ├── case-14.json         # Synthetic unknown-coverage case
│   │   ├── case-15.json         # Synthetic unknown-coverage case
│   │   ├── case-16.json         # Synthetic network case
│   │   ├── case-17.json         # Synthetic network case
│   │   ├── case-18.json         # Synthetic max-allowed case
│   │   ├── case-19.json         # Synthetic max-allowed case
│   │   ├── case-20.json         # Synthetic high-amount case
│   │   ├── case-21.json         # Synthetic high-amount case
│   │   ├── case-22.json         # Synthetic duplicate case
│   │   ├── case-23.json         # Synthetic duplicate case
│   │   ├── case-24.json         # Synthetic duplicate case
│   │   ├── case-25.json         # Synthetic timely-filing case
│   │   ├── case-26.json         # Synthetic timely-filing case
│   │   ├── case-27.json         # Synthetic annual-limit case
│   │   ├── case-28.json         # Synthetic annual-limit case
│   │   ├── case-29.json         # Synthetic required-document case
│   │   ├── case-30.json         # Synthetic required-document case
│   │   ├── case-31.json         # Synthetic extraction discrepancy case
│   │   ├── case-32.json         # Synthetic extraction discrepancy case
│   │   ├── case-33.json         # Synthetic extraction discrepancy case
│   │   ├── case-34.json         # Synthetic submission injection case
│   │   ├── case-35.json         # Synthetic submission injection case
│   │   ├── case-36.json         # Synthetic submission injection case
│   │   ├── case-37.json         # Synthetic claim-note injection case
│   │   ├── case-38.json         # Synthetic claim-note injection case
│   │   ├── case-39.json         # Synthetic hidden-character case
│   │   ├── case-40.json         # Synthetic terminal-status case
│   │   ├── case-41.json         # Synthetic unreadable-submission case
│   │   ├── case-42.json         # Synthetic unreadable-submission case
│   │   └── case-43.json         # Synthetic missing-claim case
│   ├── cassettes/
│   │   ├── cassette.jsonl       # Replay responses for model calls
│   │   └── test_cassette.jsonl  # Small cassette used by unit tests
│   ├── policy/
│   │   └── clauses.md           # Synthetic policy corpus for BM25 retrieval
│   ├── rules/
│   │   └── required_documents.json # Procedure document requirements
│   └── schema_from_project_a.sql # Scenario DB schema provenance
├── docs/
│   ├── EXPLAIN-PLAIN-ENGLISH.md # Workflow and checkpointing explanation
│   ├── decisions.md             # Architecture decision records
│   ├── eval_findings.md         # Oracle and replay evaluation notes
│   ├── running_with_project_a.md # MCP integration runbook
│   └── sdk_notes.md             # Verified SDK and framework behavior
├── evals/
│   ├── __init__.py              # Evaluation package
│   ├── gates.json               # Evaluation metric thresholds
│   ├── reports/
│   │   ├── latest.json          # Latest measured evaluation results
│   │   └── latest.md            # Human-readable evaluation report
│   └── run_eval.py              # Oracle, replay, and live evaluation runner
├── prompts/
│   ├── assess_v1.md             # Versioned policy-assessment prompt
│   └── extract_v1.md             # Versioned claim-extraction prompt
├── scripts/
│   ├── build_scenario_db.py     # Build synthetic claims scenario database
│   └── record_cassettes.py      # Record model responses with explicit opt-in
├── src/triage/
│   ├── __init__.py              # Package initializer
│   ├── __main__.py              # Uvicorn entry point
│   ├── api.py                   # FastAPI endpoints and API-key enforcement
│   ├── config.py                # Environment configuration and validation
│   ├── graph.py                 # LangGraph topology and node telemetry
│   ├── injection.py             # Injection-pattern scanner
│   ├── llm.py                   # Gemini adapter, replay, and run budgets
│   ├── logging_setup.py         # Allowlisted JSON log formatter
│   ├── models.py                # Strict Pydantic data contracts
│   ├── nodes.py                 # Workflow node implementations
│   ├── retrieval.py             # Hand-written BM25 policy retrieval
│   ├── rules.py                 # Deterministic route rules
│   ├── sanitize.py              # Untrusted-text sanitization
│   ├── service.py               # Run orchestration and persistence integration
│   ├── state.py                 # JSON-serializable LangGraph state
│   ├── store.py                 # Append-only decisions and review records
│   └── tools/
│       ├── __init__.py          # Tools package initializer
│       ├── base.py              # Read-only claims tools protocol
│       ├── fixture_backend.py   # Offline fixture tools
│       └── mcp_backend.py       # MCP client for claims-mcp-server
├── tests/
│   ├── test_api.py              # API auth, routes, and response behavior
│   ├── test_cases.py            # Synthetic case and fixture validation
│   ├── test_config.py           # Configuration defaults and validation
│   ├── test_contract_mcp.py     # Optional live MCP contract checks
│   ├── test_eval_gates.py       # Evaluation gate regression checks
│   ├── test_execution_budget.py # Recursion, time, and LLM-call budgets
│   ├── test_graph.py            # Graph routing, review, and checkpointing
│   ├── test_injection.py        # Injection scanner cases
│   ├── test_llm.py              # Gemini adapter and cassette behavior
│   ├── test_logging.py          # Safe structured logging
│   ├── test_logging_runtime.py  # API runtime telemetry regression checks
│   ├── test_logging_telemetry.py # Node telemetry and privacy checks
│   ├── test_mcp_backend.py      # MCP client behavior with fakes
│   ├── test_models.py           # Strict model validation
│   ├── test_nodes_a.py          # Intake, extraction, and fact nodes
│   ├── test_nodes_b.py          # Retrieval, assessment, review, finalize
│   ├── test_readme_tree.py      # README tree path existence
│   ├── test_retrieval.py        # BM25 correctness and determinism
│   ├── test_rules.py             # Deterministic route rules
│   ├── test_sanitize.py         # Untrusted-text sanitization
│   ├── test_smoke.py            # Package import smoke test
│   └── test_store_service.py    # Persistence and idempotency
├── pyproject.toml               # Package and tooling configuration
└── uv.lock                      # Reproducible dependency lockfile
```

## Logging and guardrails

The workflow uses four execution/safety layers:

1. **Input and injection scan:** submission and note text are sanitized and scanned before any LLM call; an injection flag drives the deterministic route to escalation.
2. **Deterministic rules gate:** only `rules.py` chooses the route. Gemini output cannot independently select a route.
3. **Citation validation:** assessment citations must refer to retrieved clauses, and supporting/restricting quotes must match the source text.
4. **Execution budget:** graph recursion is capped by `MAX_GRAPH_STEPS` (25 by default), each run has a `RUN_TIMEOUT_SECONDS` deadline (60 by default), and model requests are capped by `MAX_LLM_CALLS_PER_RUN` (4 by default). A breach returns a persisted `ESCALATE` decision and emits `budget_exhausted`.

Node telemetry is a single flat JSON object per event. Claim and note text, prompts, and model output are not fields in the log event.

| Field | Meaning |
|---|---|
| `timestamp` | UTC event time |
| `run_id` | Graph thread identifier |
| `claim_id` | Synthetic claim identifier |
| `node_name` | Node that emitted the event |
| `model_name` | Configured model for LLM nodes; `none` otherwise |
| `input_tokens` / `output_tokens` | Usage reported for that node |
| `cost_usd` | Estimated model cost for the node |
| `latency_ms` | Node duration in milliseconds |
| `guardrail_triggered` | Whether any guardrail flag was raised |
| `guardrail_flags` | Short codes such as `injection_detected`, `policy_gate`, or `budget_exhausted` |
| `final_route` | Current or final route when available |

Real sample line captured during the API telemetry test in this session:

```json
{"timestamp": "2026-11-01T05:28:11.114423+00:00", "level": "INFO", "logger": "triage.workflow", "node_name": "decide", "output_tokens": 0, "guardrail_triggered": false, "cost_usd": 0.0, "latency_ms": 0.18, "claim_id": "CLM70000001", "model_name": "none", "final_route": "FAST_TRACK", "input_tokens": 0, "guardrail_flags": [], "run_id": "triage-CLM70000001-v1"}
```

## Evaluation and limitations

The replay evaluation run in this session passed all configured gates: false fast-track rate `0.0000`, route accuracy `1.0000`, injection containment `1.0000`, citation validity `1.0000`, rule-ID match `1.0000`, and reason coverage `1.0000`. Oracle mode feeds the gold extraction and assessment into the graph; it checks rules, graph behavior, and gates, not Gemini quality. Replay mode exercises recorded Gemini output without network access. The expected labels derive from the same rule specification, so these evaluations measure consistency with the contract rather than independent clinical ground truth.

Pattern scanning is a tripwire, not a complete injection defense. The reviewer identity is self-declared; pending reviews have no SLA; graph runs are serialized in one process; fast-tracked claims are not sampled for QA; BM25 can miss vocabulary mismatches; the synthetic evaluation does not establish real-world performance; Gemini free-tier limits and data-use terms apply; and the local API does not provide TLS.

For integration steps using the read-only MCP backend, see [`docs/running_with_project_a.md`](docs/running_with_project_a.md).
