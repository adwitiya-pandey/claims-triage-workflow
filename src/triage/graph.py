"""LangGraph workflow definition for claims triage.

Graph Topology (SPEC 5):
START -> intake -> extract -> fetch_facts -> retrieve -> assess -> decide
From decide:
  - Conditional edge:
      route == "FAST_TRACK" -> finalize -> END
      route != "FAST_TRACK" -> review -> finalize -> END

Node specifications:
- intake: Validate claim ID, load submission, sanitize, injection scan (no LLM).
- extract: Extract fields using Gemini via Extraction schema, one retry, else extraction_failed (LLM).
- fetch_facts: Query Project A read-only tools, normalize, detect duplicates, re-scan notes (no LLM).
- retrieve: BM25 retrieval over policy clauses + procedure clause (no LLM).
- assess: LLM policy assessment, then deterministic verbatim quote citation checks (LLM).
- decide: Pure deterministic rule engine (section 11): the ONLY place a route is chosen (no LLM).
- review: Human review gate via durable interrupt(packet); resume with ReviewAction (human).
- finalize: Build and persist the auditable decision record to SQLite (no LLM).

LangGraph interrupt() rules (AGENTS Gotcha 2):
1. On resume, the review node restarts from its very first line.
2. The review node builds the review packet, calls interrupt(packet), validates the resume value,
   and returns.
3. No non-idempotent side effects before interrupt().
4. No broad try/except around interrupt().
5. No while loop around interrupt().
6. Pass only JSON-serialisable values into interrupt().
7. Resume with Command(resume=value) and the same thread_id.
8. Do not use Command(update=...) as input.
9. Do not use the response_schema= option of interrupt().
"""

from typing import Any


def build_graph(nodes: dict[str, Any], checkpointer: Any = None) -> Any:
    """Build and compile the LangGraph claims triage state graph.

    Args:
        nodes: Dictionary of node callables returned by make_nodes().
        checkpointer: LangGraph checkpointer (e.g. SqliteSaver or InMemorySaver).

    Returns:
        Compiled LangGraph graph object.
    """
    raise NotImplementedError("build_graph will be implemented in T11")
