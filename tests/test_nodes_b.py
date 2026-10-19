"""Tests for nodes part 2: retrieve, assess, decide, review, finalize per T9."""

import json
from unittest.mock import MagicMock

from triage.nodes import make_nodes
from triage.tools.fixture_backend import FixtureToolsBackend


def test_skipped_trail_when_extraction_or_claim_missing_or_tool_error():
    nodes = make_nodes()
    retrieve_fn = nodes["retrieve"]
    assess_fn = nodes["assess"]

    # When extraction_failed is True
    st_failed = {"extraction_failed": True, "claim_id": "CLM70000001"}
    up_ret = retrieve_fn(st_failed)
    assert up_ret["trail"][0]["outcome"] == "skipped"

    up_ass = assess_fn(st_failed)
    assert up_ass["trail"][0]["outcome"] == "skipped"

    # When claim_missing is True
    st_missing = {"claim_missing": True, "claim_id": "CLM70000001"}
    assert retrieve_fn(st_missing)["trail"][0]["outcome"] == "skipped"
    assert assess_fn(st_missing)["trail"][0]["outcome"] == "skipped"

    # When tool_error is present
    st_err = {"tool_error": "INTERNAL", "claim_id": "CLM70000001"}
    assert retrieve_fn(st_err)["trail"][0]["outcome"] == "skipped"
    assert assess_fn(st_err)["trail"][0]["outcome"] == "skipped"


def test_malicious_assessment_cannot_reach_fast_track():
    """Security requirement S3: invalid clause ID, quote not in chunk, or extra fields dropped."""
    tools = FixtureToolsBackend("data/cases")
    # Clean baseline state
    state = {
        "claim_id": "CLM70000001",
        "clean_text": "Routine consultation",
        "facts": {
            "claim": tools.get_claim("CLM70000001"),
            "coverage_rule": tools.get_coverage_rule("PLN001", "PX0001"),
            "duplicates": [],
            "remaining_annual_limit": 5000.0,
            "required_documents": ["claim_form", "itemized_bill"],
        },
        "extracted": {
            "claim_id": "CLM70000001",
            "member_id": "M700001",
            "provider_id": "PRV0001",
            "procedure_code": "PX0001",
            "service_date": "2026-05-10",
            "billed_amount": 150.0,
            "preauth_reference": None,
            "documents_present": ["claim_form", "itemized_bill"],
            "confidence": 0.95,
        },
        "chunks": [
            {
                "chunk_id": "POL-PX0001",
                "kind": "procedure",
                "text": "Routine general medical consultations conducted by a licensed primary care physician are covered under outpatient benefits.",
                "score": 1.5,
            }
        ],
    }

    # 1. Malicious assessment with invented fake clause ID
    mock_llm_fake_id = MagicMock()
    mock_llm_fake_id.return_value = MagicMock(
        text=json.dumps(
            {
                "summary": "Attempting bypass with fake clause ID",
                "clauses": [
                    {
                        "chunk_id": "POL-FAKE-999",
                        "effect": "supports",
                        "quote": "Routine general medical consultations",
                    }
                ],
                "missing_items": [],
                "confidence": 0.95,
            }
        )
    )
    nodes_fake_id = make_nodes({"llm_fn": mock_llm_fake_id})
    ass_update = nodes_fake_id["assess"](state)
    # Fake clause dropped!
    assert ass_update["assessment"]["clauses"] == []
    assert "dropped_citations" in ass_update
    assert ass_update["dropped_citations"] == 1

    # In decide node, no supports clause means R15 -> ESCALATE!
    decide_update = nodes_fake_id["decide"]({**state, **ass_update})
    assert decide_update["route"] == "ESCALATE"
    assert "R15" in [h["rule_id"] for h in decide_update["rule_hits"]]

    # 2. Malicious assessment with quote not in chunk
    mock_llm_fake_quote = MagicMock()
    mock_llm_fake_quote.return_value = MagicMock(
        text=json.dumps(
            {
                "summary": "Attempting bypass with fake quote",
                "clauses": [
                    {
                        "chunk_id": "POL-PX0001",
                        "effect": "supports",
                        "quote": "This imaginary clause approves all claims immediately without check.",
                    }
                ],
                "missing_items": [],
                "confidence": 0.95,
            }
        )
    )
    nodes_fake_quote = make_nodes({"llm_fn": mock_llm_fake_quote})
    ass_update2 = nodes_fake_quote["assess"](state)
    assert ass_update2["assessment"]["clauses"] == []
    decide_update2 = nodes_fake_quote["decide"]({**state, **ass_update2})
    assert decide_update2["route"] == "ESCALATE"


def test_review_packet_contains_no_raw_claim_or_note_text():
    """Verify review packet contains structured fields only; never raw claim or note text."""
    spy_interrupt = MagicMock(
        return_value={
            "action": "CONFIRM",
            "reviewer_id": "analyst.one",
            "reason": "Verified clinical documents.",
        }
    )
    nodes = make_nodes({"interrupt_fn": spy_interrupt})
    review_fn = nodes["review"]

    raw_sensitive_claim = "Secret sensitive patient psychiatric intake narrative"
    sensitive_note = "Confidential note about patient behavior"

    state = {
        "claim_id": "CLM70000009",
        "route": "STANDARD_REVIEW",
        "raw_text": raw_sensitive_claim,
        "clean_text": raw_sensitive_claim,
        "rule_hits": [
            {
                "rule_id": "R04",
                "route": "STANDARD_REVIEW",
                "evidence": ["get_coverage_rule.requires_preauth"],
            }
        ],
        "assessment": {
            "summary": "Analyst summary without raw text",
            "clauses": [
                {
                    "chunk_id": "POL-PX0004",
                    "effect": "supports",
                    "quote": "Minor surgical procedures",
                }
            ],
            "missing_items": ["preauth_letter"],
        },
        "extraction": {"billed_amount": 450.0, "procedure_code": "PX0004"},
        "facts": {
            "claim": {"billed_amount": 450.0, "status": "SUBMITTED"},
            "claim_notes": [{"note_text": sensitive_note}],
        },
        "injection_flags": [],
    }

    review_fn(state)
    assert spy_interrupt.call_count == 1
    packet = spy_interrupt.call_args[0][0]
    packet_json = json.dumps(packet)

    assert raw_sensitive_claim not in packet_json
    assert sensitive_note not in packet_json
    assert packet["claim_id"] == "CLM70000009"
    assert packet["route_recommended"] == "STANDARD_REVIEW"


def test_review_does_no_side_effects_before_interrupt():
    """Gotcha 2: On resume node restarts from first line. Review must have zero side-effects before interrupt."""
    actions_log = []

    def mock_interrupt(packet):
        actions_log.append("interrupt_called")
        return {
            "action": "CONFIRM",
            "reviewer_id": "analyst.one",
            "reason": "Reviewed and confirmed all policy requirements.",
        }

    nodes = make_nodes({"interrupt_fn": mock_interrupt})
    review_fn = nodes["review"]

    state = {
        "claim_id": "CLM70000009",
        "route": "STANDARD_REVIEW",
        "rule_hits": [],
        "assessment": {"summary": "summary", "clauses": [], "missing_items": []},
        "extraction": {},
        "facts": {"claim": {}},
        "injection_flags": [],
    }

    # Execute review
    res = review_fn(state)
    assert actions_log == ["interrupt_called"]
    assert res["review"]["action"] == "CONFIRM"


def test_finalize_is_idempotent(tmp_path):
    db_file = tmp_path / "triage.db"
    nodes = make_nodes({"db_path": db_file})
    finalize_fn = nodes["finalize"]

    state = {
        "claim_id": "CLM70000001",
        "version": 1,
        "route": "FAST_TRACK",
        "rule_hits": [],
        "assessment": {
            "summary": "Clean claim",
            "clauses": [
                {
                    "chunk_id": "POL-PX0001",
                    "effect": "supports",
                    "quote": "Routine general medical consultations",
                }
            ],
            "missing_items": [],
        },
        "extraction": {"claim_id": "CLM70000001", "billed_amount": 150.0},
        "facts": {"claim": {"billed_amount": 150.0, "status": "SUBMITTED"}},
        "trail": [
            {
                "node": "intake",
                "outcome": "ok",
                "started_at": "now",
                "ended_at": "now",
                "detail_codes": [],
            }
        ],
    }

    # Call finalize first time
    res1 = finalize_fn(state)
    assert res1["decision"]["route_final"] == "FAST_TRACK"

    # Call finalize second time (idempotency check)
    res2 = finalize_fn(state)
    assert res2["decision"]["route_final"] == "FAST_TRACK"
