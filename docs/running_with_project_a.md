# Running with Project A (MCP Server Integration)

This document provides the exact end-to-end instructions for spinning up the Project A MCP server (`claims-mcp-server`), provisioning a dedicated Bearer token for Project B (`claims-triage-workflow`), and executing the live contract integration tests.

---

## 1. Prerequisites

- Project A is located at `~/projects/claims-mcp-server`.
- Project B is located at `~/projects/claims-triage-workflow`.
- Python 3.12+ and `uv` package manager installed.

---

## 2. Step 1: Build the Scenario Database

From Project B (`claims-triage-workflow`), run the scenario database builder script. This script executes Project A's `schema.sql` (mirrored at `data/schema_from_project_a.sql`) and populates `data/scenario.db` with all 43 scenario cases:

```bash
cd ~/projects/claims-triage-workflow
uv run python scripts/build_scenario_db.py
```

This creates `data/scenario.db` populated with all members, providers, claims, coverage rules, YTD summaries, and claim notes.

---

## 3. Step 2: Create an API Key for Project B in Project A

Project A uses scoped Bearer keys configured in `keys.json`. Generate an API key restricted strictly to the five read-only tools required by Project B:

```bash
cd ~/projects/claims-mcp-server

# Use make_key.py to generate a key for 'claims-triage-b' with the 5 read tools
uv run python scripts/make_key.py \
  --caller claims-triage-b \
  --tools get_claim,list_member_claims,get_coverage_rule,get_member_ytd_summary,get_claim_notes \
  --keys-file keys.json
```

Copy the generated plain key string (e.g. `key_live_...`).

---

## 4. Step 3: Start Project A MCP Server on the Scenario Database

Start Project A HTTP transport server pointing to the built scenario database:

```bash
cd ~/projects/claims-mcp-server

uv run python -m claims_mcp.server \
  --transport http \
  --host 127.0.0.1 \
  --port 8000 \
  --db-path ~/projects/claims-triage-workflow/data/scenario.db \
  --keys-file keys.json \
  --audit-path audit/audit.jsonl
```

The MCP server is now listening at `http://127.0.0.1:8000/mcp`.

---

## 5. Step 4: Run the Contract Integration Test

In Project B, export the server URL and Bearer key, then run the integration test:

```bash
cd ~/projects/claims-triage-workflow

export CLAIMS_MCP_URL="http://127.0.0.1:8000/mcp"
export CLAIMS_MCP_KEY="<paste-plain-key-here>"

# Run the integration test
uv run pytest tests/test_contract_mcp.py -m integration -v
```

The contract test verifies that every case in `data/cases/*.json` produces identical responses between Project B's offline fixture backend and Project A's live MCP endpoints.
