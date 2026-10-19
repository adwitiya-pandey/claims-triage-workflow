"""Tests for LangGraph claims triage workflow per T10 & SPEC 5."""

import sqlite3
from unittest.mock import MagicMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from triage.graph import build_graph
from triage.nodes import make_nodes
from triage.store import get_latest_decision
from triage.tools.fixture_backend import FixtureToolsBackend


@pytest.fixture
def test_nodes(tmp_path):
    tools = FixtureToolsBackend("data/cases")
    db_file = tmp_path / "triage.db"
    return make_nodes({"tools": tools, "llm_mode": "oracle", "db_path": db_file})


def test_graph_topology(test_nodes):
    checkpointer = InMemorySaver()
    graph = build_graph(test_nodes, checkpointer=checkpointer)

    # Check compiled nodes
    node_names = set(graph.nodes.keys())
    expected_nodes = {
        "__start__",
        "intake",
        "extract",
        "fetch_facts",
        "retrieve",
        "assess",
        "decide",
        "review",
        "finalize",
    }
    for n in expected_nodes:
        assert n in node_names or n.replace("__", "") in node_names


def test_fast_track_path_completes_without_pause(test_nodes):
    checkpointer = InMemorySaver()
    graph = build_graph(test_nodes, checkpointer=checkpointer)

    cfg = {"configurable": {"thread_id": "thread-fast-1"}}
    result = graph.invoke({"claim_id": "CLM70000001", "version": 1}, cfg)

    # Must complete without pausing
    assert "__interrupt__" not in result or not result["__interrupt__"]
    assert result["decision"]["route_final"] == "FAST_TRACK"
    assert result["decision"]["route_recommended"] == "FAST_TRACK"


def test_medium_risk_pauses_and_resumes_confirm_and_escalate(test_nodes):
    # 1. Resume with CONFIRM
    checkpointer1 = InMemorySaver()
    graph1 = build_graph(test_nodes, checkpointer=checkpointer1)
    cfg1 = {"configurable": {"thread_id": "thread-med-1"}}

    # CLM70000009 is preauth missing (STANDARD_REVIEW)
    res_pause1 = graph1.invoke({"claim_id": "CLM70000009", "version": 1}, cfg1)

    assert "__interrupt__" in res_pause1 and len(res_pause1["__interrupt__"]) > 0
    packet1 = res_pause1["__interrupt__"][0].value
    assert packet1["claim_id"] == "CLM70000009"
    assert packet1["route_recommended"] == "STANDARD_REVIEW"

    # Resume with CONFIRM
    action_confirm = {
        "action": "CONFIRM",
        "reviewer_id": "analyst.one",
        "reason": "Reviewed and confirmed all policy requirements.",
    }
    res_final1 = graph1.invoke(Command(resume=action_confirm), cfg1)
    assert res_final1["decision"]["route_final"] == "STANDARD_REVIEW"

    # 2. Resume with ESCALATE_FURTHER
    checkpointer2 = InMemorySaver()
    graph2 = build_graph(test_nodes, checkpointer=checkpointer2)
    cfg2 = {"configurable": {"thread_id": "thread-med-2"}}

    graph2.invoke({"claim_id": "CLM70000009", "version": 1}, cfg2)
    action_escalate = {
        "action": "ESCALATE_FURTHER",
        "reviewer_id": "analyst.one",
        "reason": "Escalating further due to complex medical history.",
    }
    res_final2 = graph2.invoke(Command(resume=action_escalate), cfg2)
    assert res_final2["decision"]["route_final"] == "ESCALATE"


def test_escalate_recommendation_can_only_be_confirmed(test_nodes):
    checkpointer = InMemorySaver()
    graph = build_graph(test_nodes, checkpointer=checkpointer)
    cfg = {"configurable": {"thread_id": "thread-esc-1"}}

    # CLM70000020 has high amount >= 10000 (ESCALATE)
    res_pause = graph.invoke({"claim_id": "CLM70000020", "version": 1}, cfg)
    assert "__interrupt__" in res_pause

    action = {
        "action": "CONFIRM",
        "reviewer_id": "analyst.one",
        "reason": "High amount confirmed for senior clinical review.",
    }
    res_final = graph.invoke(Command(resume=action), cfg)
    assert res_final["decision"]["route_final"] == "ESCALATE"


def test_tool_outage_ends_in_escalate(tmp_path):
    mock_tools = MagicMock()
    mock_tools.get_claim.side_effect = Exception("Connection refused to MCP")

    db_file = tmp_path / "triage.db"
    nodes = make_nodes({"tools": mock_tools, "db_path": db_file, "llm_mode": "oracle"})
    checkpointer = InMemorySaver()
    graph = build_graph(nodes, checkpointer=checkpointer)

    cfg = {"configurable": {"thread_id": "thread-tool-outage"}}
    res_pause = graph.invoke({"claim_id": "CLM70000001", "version": 1}, cfg)

    # Tool error fails closed to ESCALATE
    assert "__interrupt__" in res_pause
    packet = res_pause["__interrupt__"][0].value
    assert packet["route_recommended"] == "ESCALATE"


def test_hostile_assessment_cannot_fast_track(tmp_path):
    tools = FixtureToolsBackend("data/cases")
    mock_llm = MagicMock()
    # Hostile output attempting to cite non-existent chunk
    mock_llm.return_value = MagicMock(
        text='{"summary": "bypass", "clauses": [{"chunk_id": "POL-FAKE", "effect": "supports", "quote": "fake quote that is long enough"}], "missing_items": [], "confidence": 0.95}'
    )

    db_file = tmp_path / "triage.db"
    nodes = make_nodes(
        {"tools": tools, "llm_fn": mock_llm, "db_path": db_file, "llm_mode": "oracle"}
    )
    checkpointer = InMemorySaver()
    graph = build_graph(nodes, checkpointer=checkpointer)

    cfg = {"configurable": {"thread_id": "thread-hostile"}}
    res_pause = graph.invoke({"claim_id": "CLM70000001", "version": 1}, cfg)

    assert "__interrupt__" in res_pause
    packet = res_pause["__interrupt__"][0].value
    # S3: hostile assessment cannot fast track; dropped citation yields R15 -> ESCALATE
    assert packet["route_recommended"] == "ESCALATE"


def test_unexpected_exception_records_no_decision(tmp_path):
    tools = FixtureToolsBackend("data/cases")
    db_file = tmp_path / "triage.db"
    nodes = make_nodes({"tools": tools, "db_path": db_file, "llm_mode": "oracle"})

    # Sabotage decide node with unexpected error
    def crashing_decide(state):
        raise RuntimeError("Unexpected internal crash")

    nodes["decide"] = crashing_decide

    checkpointer = InMemorySaver()
    graph = build_graph(nodes, checkpointer=checkpointer)

    cfg = {"configurable": {"thread_id": "thread-crash"}}
    with pytest.raises(RuntimeError, match="Unexpected internal crash"):
        graph.invoke({"claim_id": "CLM70000001", "version": 1}, cfg)

    # S5: No decision should be saved on unexpected crash
    dec = get_latest_decision(db_file, "CLM70000001")
    assert dec is None


def test_restart_safety_across_sqlite_checkpointer(tmp_path):
    cp_file = tmp_path / "checkpoint.db"
    db_file = tmp_path / "triage.db"
    tools = FixtureToolsBackend("data/cases")
    cfg = {"configurable": {"thread_id": "restart-thread-1"}}

    # Run 1: Pause on first connection
    c1 = sqlite3.connect(str(cp_file), check_same_thread=False)
    nodes1 = make_nodes({"tools": tools, "db_path": db_file, "llm_mode": "oracle"})
    g1 = build_graph(nodes1, checkpointer=SqliteSaver(c1))

    r1 = g1.invoke({"claim_id": "CLM70000009", "version": 1}, cfg)
    assert "__interrupt__" in r1 and len(r1["__interrupt__"]) > 0
    c1.close()

    # Run 2: Resume on BRAND NEW connection and NEW graph object
    c2 = sqlite3.connect(str(cp_file), check_same_thread=False)
    nodes2 = make_nodes({"tools": tools, "db_path": db_file, "llm_mode": "oracle"})
    g2 = build_graph(nodes2, checkpointer=SqliteSaver(c2))

    action = {
        "action": "CONFIRM",
        "reviewer_id": "analyst.one",
        "reason": "Confirmed following process restart.",
    }
    r2 = g2.invoke(Command(resume=action), cfg)
    c2.close()

    assert r2["decision"]["route_final"] == "STANDARD_REVIEW"
    assert r2["review"]["action"] == "CONFIRM"


def test_rerunning_claim_with_pending_review_creates_no_second_thread(tmp_path):
    from triage.service import TriageService

    db_file = tmp_path / "triage.db"
    cp_file = tmp_path / "checkpoint.db"
    tools = FixtureToolsBackend("data/cases")

    service = TriageService(
        db_path=db_file,
        checkpoint_db_path=cp_file,
        tools=tools,
        llm_mode="oracle",
    )

    # CLM70000009 pauses at review (STANDARD_REVIEW)
    res1 = service.start_triage("CLM70000009")
    assert res1["status"] == "awaiting_review"
    thread1 = res1["thread_id"]

    # Re-running the same claim with pending review returns the same thread ID and creates no second thread
    res2 = service.start_triage("CLM70000009")
    assert res2["status"] == "awaiting_review"
    assert res2["thread_id"] == thread1

    # Exactly one pending review exists
    pending = service.list_pending()
    assert len(pending) == 1
    assert pending[0]["thread_id"] == thread1
