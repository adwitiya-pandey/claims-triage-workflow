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

from langgraph.graph import END, START, StateGraph

from triage.state import GraphState


def _route_condition(state: GraphState) -> str:
    """Determine whether to route to finalize (fast-track) or review (human gate)."""
    if state.get("route") == "FAST_TRACK":
        return "finalize"
    return "review"


def build_graph(nodes: dict[str, Any], checkpointer: Any = None) -> Any:
    """Build and compile the LangGraph claims triage state graph.

    Args:
        nodes: Dictionary of node callables returned by make_nodes().
        checkpointer: LangGraph checkpointer (e.g. SqliteSaver or InMemorySaver).

    Returns:
        Compiled LangGraph graph object.
    """
    workflow = StateGraph(GraphState)

    workflow.add_node("intake", nodes["intake"])
    workflow.add_node("extract", nodes["extract"])
    workflow.add_node("fetch_facts", nodes["fetch_facts"])
    workflow.add_node("retrieve", nodes["retrieve"])
    workflow.add_node("assess", nodes["assess"])
    workflow.add_node("decide", nodes["decide"])
    workflow.add_node("review", nodes["review"])
    workflow.add_node("finalize", nodes["finalize"])

    workflow.add_edge(START, "intake")
    workflow.add_edge("intake", "extract")
    workflow.add_edge("extract", "fetch_facts")
    workflow.add_edge("fetch_facts", "retrieve")
    workflow.add_edge("retrieve", "assess")
    workflow.add_edge("assess", "decide")

    workflow.add_conditional_edges(
        "decide",
        _route_condition,
        {
            "finalize": "finalize",
            "review": "review",
        },
    )

    workflow.add_edge("review", "finalize")
    workflow.add_edge("finalize", END)

    return workflow.compile(checkpointer=checkpointer)
