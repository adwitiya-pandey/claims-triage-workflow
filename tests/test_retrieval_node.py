"""Tests for state, review-packet, and fallback behavior of policy retrieval."""

from triage.config import TriageConfig
from triage.nodes import NodeDependencies, make_nodes
from triage.tools.fixture_backend import FixtureToolsBackend


def _retrieval_state():
    return {
        "claim_id": "CLM70000001",
        "extraction": {"procedure_description": "routine consultation"},
        "facts": {"claim": {"procedure_code": "PX0001"}},
    }


def test_retrieve_falls_back_to_bm25_and_marks_guardrail():
    class BrokenEmbedder:
        def embed_query(self, text):
            raise RuntimeError("offline test failure")

    nodes = make_nodes(
        NodeDependencies(
            tools=FixtureToolsBackend(),
            config=TriageConfig(LLM_MODE="oracle", RETRIEVER="hybrid"),
            embedder=BrokenEmbedder(),
            vector_store=object(),
        )
    )

    result = nodes["retrieve"](_retrieval_state())

    assert result["chunks"][0]["chunk_id"] == "POL-PX0001"
    assert result["retriever_mode"] == "bm25"
    assert result["guardrail_flags"] == ["dense_unavailable"]
    assert result["trail"][0]["detail_codes"] == ["dense_unavailable"]
    assert result["retrieval_ms"] >= 0


def test_review_packet_includes_only_compact_retrieval_trace():
    captured = {}

    def interrupt(packet):
        captured.update(packet)
        return {
            "reviewer_id": "analyst.alice",
            "action": "CONFIRM",
            "reason": "Reviewed the synthetic clause ranking.",
        }

    nodes = make_nodes(
        NodeDependencies(
            tools=FixtureToolsBackend(),
            config=TriageConfig(LLM_MODE="oracle"),
        ),
        interrupt_fn=interrupt,
    )
    trace = [
        {
            "chunk_id": "POL-PX0001",
            "bm25_rank": None,
            "bm25_score": 0.2,
            "dense_rank": None,
            "dense_score": None,
            "fused_score": None,
        }
    ]

    nodes["review"](
        {
            "claim_id": "CLM70000009",
            "route": "STANDARD_REVIEW",
            "rule_hits": [],
            "assessment": {"clauses": [], "missing_items": []},
            "facts": {"claim": {}},
            "extraction": {},
            "injection_flags": [],
            "retrieval_trace": trace,
        }
    )

    assert captured["retrieval_trace"] == trace
    assert "text" not in captured["retrieval_trace"][0]
