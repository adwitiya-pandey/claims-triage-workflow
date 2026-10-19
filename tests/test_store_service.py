"""Tests for SQLite persistence, append-only rules, and TriageService per T13 & SPEC 13."""

from pathlib import Path

import pytest

from triage.service import ConflictError, NotFoundError, TriageService
from triage.store import list_decisions
from triage.tools.fixture_backend import FixtureToolsBackend


def test_decisions_table_is_strictly_append_only():
    """Security requirement S5 & SPEC 13: decisions table is append-only.

    Scan store.py source to ensure no UPDATE or DELETE on decisions exists.
    """
    store_code = Path("src/triage/store.py").read_text(encoding="utf-8")
    assert "UPDATE decisions" not in store_code, "Found forbidden UPDATE on decisions table!"
    assert "DELETE FROM decisions" not in store_code, "Found forbidden DELETE on decisions table!"


def test_triage_service_idempotency_for_completed_claim(tmp_path):
    """Calling start_triage multiple times on a completed claim returns existing decision."""
    db_file = tmp_path / "triage.db"
    cp_file = tmp_path / "checkpoint.db"
    tools = FixtureToolsBackend("data/cases")

    service = TriageService(
        db_path=db_file,
        checkpoint_db_path=cp_file,
        tools=tools,
        llm_mode="oracle",
    )

    # CLM70000001 is a clean FAST_TRACK claim
    res1 = service.start_triage("CLM70000001", reprocess=False)
    assert res1["status"] == "completed"
    dec1 = res1["decision"]
    assert dec1["route_final"] == "FAST_TRACK"
    assert dec1["version"] == 1

    # Second call without reprocess returns the exact same version 1 decision
    res2 = service.start_triage("CLM70000001", reprocess=False)
    assert res2["status"] == "completed"
    assert res2["decision"]["version"] == 1
    assert res2["decision"]["claim_id"] == "CLM70000001"

    # Only one decision record in DB
    all_decisions = list_decisions(db_file, "CLM70000001")
    assert len(all_decisions) == 1


def test_triage_service_reprocess_increments_version(tmp_path):
    """Calling start_triage with reprocess=True creates version n+1."""
    db_file = tmp_path / "triage.db"
    cp_file = tmp_path / "checkpoint.db"
    tools = FixtureToolsBackend("data/cases")

    service = TriageService(
        db_path=db_file,
        checkpoint_db_path=cp_file,
        tools=tools,
        llm_mode="oracle",
    )

    # Version 1
    res1 = service.start_triage("CLM70000001", reprocess=False)
    assert res1["decision"]["version"] == 1

    # Version 2
    res2 = service.start_triage("CLM70000001", reprocess=True)
    assert res2["status"] == "completed"
    assert res2["decision"]["version"] == 2

    # Version 3
    res3 = service.start_triage("CLM70000001", reprocess=True)
    assert res3["status"] == "completed"
    assert res3["decision"]["version"] == 3

    # Decisions listed newest first
    decisions = service.get_decisions("CLM70000001")
    assert len(decisions) == 3
    assert [d["version"] for d in decisions] == [3, 2, 1]


def test_pending_index_lifecycle_and_submit_review(tmp_path):
    """Review lifecycle: pending index reflects awaiting reviews, submit_review resolves them."""
    db_file = tmp_path / "triage.db"
    cp_file = tmp_path / "checkpoint.db"
    tools = FixtureToolsBackend("data/cases")

    service = TriageService(
        db_path=db_file,
        checkpoint_db_path=cp_file,
        tools=tools,
        llm_mode="oracle",
    )

    # Initial pending list is empty
    assert service.list_pending() == []

    # CLM70000009 pauses at review (STANDARD_REVIEW)
    res = service.start_triage("CLM70000009")
    assert res["status"] == "awaiting_review"
    thread_id = res["thread_id"]

    # Pending index contains exactly this review
    pending = service.list_pending()
    assert len(pending) == 1
    assert pending[0]["thread_id"] == thread_id
    assert pending[0]["claim_id"] == "CLM70000009"
    assert pending[0]["status"] == "awaiting"

    # Submit review
    review_action = {
        "action": "CONFIRM",
        "reviewer_id": "senior.auditor",
        "reason": "Reviewed and confirmed standard routing.",
    }
    submit_res = service.submit_review(thread_id, review_action)
    assert submit_res["status"] == "completed"
    assert submit_res["decision"]["route_final"] == "STANDARD_REVIEW"

    # Pending index is now empty
    assert service.list_pending() == []


def test_submit_review_invalid_thread_and_conflict(tmp_path):
    """Submitting review for non-existent thread raises NotFoundError; duplicate raises ConflictError."""
    db_file = tmp_path / "triage.db"
    cp_file = tmp_path / "checkpoint.db"
    tools = FixtureToolsBackend("data/cases")

    service = TriageService(
        db_path=db_file,
        checkpoint_db_path=cp_file,
        tools=tools,
        llm_mode="oracle",
    )

    action = {
        "action": "CONFIRM",
        "reviewer_id": "auditor",
        "reason": "Legitimate confirmation of standard review.",
    }

    # 1. Non-existent thread raises NotFoundError
    with pytest.raises(NotFoundError):
        service.submit_review("triage-nonexistent-v1", action)

    # 2. Pause a claim and submit review once
    res = service.start_triage("CLM70000009")
    thread_id = res["thread_id"]
    service.submit_review(thread_id, action)

    # 3. Submitting again on already resolved thread raises ConflictError
    with pytest.raises(ConflictError):
        service.submit_review(thread_id, action)


def test_get_decisions_not_found_raises_error(tmp_path):
    """Querying decisions for an unknown claim raises NotFoundError."""
    db_file = tmp_path / "triage.db"
    service = TriageService(db_path=db_file)

    with pytest.raises(NotFoundError):
        service.get_decisions("CLM79999999")
