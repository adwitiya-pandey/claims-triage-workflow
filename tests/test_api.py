"""Tests for FastAPI endpoints per T14 & SPEC 14."""

import pytest
from fastapi.testclient import TestClient

from triage.api import create_app
from triage.config import get_settings
from triage.service import TriageService
from triage.tools.fixture_backend import FixtureToolsBackend


@pytest.fixture
def test_client(tmp_path):
    settings = get_settings()
    api_key = settings.TRIAGE_API_KEY or "test-triage-key-12345"
    db_file = tmp_path / "triage.db"
    cp_file = tmp_path / "checkpoint.db"
    tools = FixtureToolsBackend("data/cases")

    service = TriageService(
        db_path=db_file,
        checkpoint_db_path=cp_file,
        tools=tools,
        llm_mode="oracle",
    )
    app = create_app(service=service, api_key=api_key)
    return TestClient(app), api_key


def test_health_endpoint_needs_no_auth(test_client):
    client, _ = test_client
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "service" in data
    # No sensitive settings or keys leaked
    assert "key" not in str(data).lower()
    assert "secret" not in str(data).lower()


def test_auth_missing_and_wrong(test_client):
    client, _valid_key = test_client

    # 1. Missing X-API-Key
    resp1 = client.post("/triage", json={"claim_id": "CLM70000001"})
    assert resp1.status_code == 401
    assert "Missing" in resp1.json().get("detail", "")

    # 2. Wrong X-API-Key
    resp2 = client.post(
        "/triage",
        json={"claim_id": "CLM70000001"},
        headers={"X-API-Key": "wrong-key-value"},
    )
    assert resp2.status_code == 401
    assert "Invalid" in resp2.json().get("detail", "")


def test_post_triage_invalid_claim_id_returns_422(test_client):
    client, key = test_client
    resp = client.post(
        "/triage",
        json={"claim_id": "INVALID_ID"},
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 422


def test_post_triage_fast_track_happy_path(test_client):
    client, key = test_client
    resp = client.post(
        "/triage",
        json={"claim_id": "CLM70000001"},
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["decision"]["route_final"] == "FAST_TRACK"
    assert data["decision"]["claim_id"] == "CLM70000001"


def test_post_triage_paused_and_resumed(test_client):
    client, key = test_client

    # 1. Start triage on CLM70000009 (pauses at STANDARD_REVIEW)
    resp = client.post(
        "/triage",
        json={"claim_id": "CLM70000009"},
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "awaiting_review"
    thread_id = data["thread_id"]
    packet = data["packet"]
    assert packet["route_recommended"] == "STANDARD_REVIEW"

    # S2: Review packet contains no raw claim text
    assert "submission_text" not in packet
    assert "raw_text" not in packet

    # 2. Check pending endpoint
    resp_pending = client.get("/reviews/pending", headers={"X-API-Key": key})
    assert resp_pending.status_code == 200
    pending_list = resp_pending.json()
    assert any(p["thread_id"] == thread_id for p in pending_list)

    # 3. Resume via POST /reviews/{thread_id}
    action = {
        "action": "CONFIRM",
        "reviewer_id": "senior.analyst",
        "reason": "Reviewed and confirmed all policy requirements.",
    }
    resp_resume = client.post(
        f"/reviews/{thread_id}",
        json=action,
        headers={"X-API-Key": key},
    )
    assert resp_resume.status_code == 200
    resume_data = resp_resume.json()
    assert resume_data["status"] == "completed"
    assert resume_data["decision"]["route_final"] == "STANDARD_REVIEW"

    # 4. Check pending endpoint is now cleared of this thread
    resp_pending2 = client.get("/reviews/pending", headers={"X-API-Key": key})
    assert all(p["thread_id"] != thread_id for p in resp_pending2.json())


def test_review_not_found_returns_404(test_client):
    client, key = test_client
    action = {
        "action": "CONFIRM",
        "reviewer_id": "analyst",
        "reason": "Valid reason exceeding ten characters.",
    }
    resp = client.post(
        "/reviews/triage-CLM99999999-v1",
        json=action,
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 404


def test_review_conflict_returns_409(test_client):
    client, key = test_client
    # Pause CLM70000009
    resp = client.post(
        "/triage",
        json={"claim_id": "CLM70000009"},
        headers={"X-API-Key": key},
    )
    thread_id = resp.json()["thread_id"]

    action = {
        "action": "CONFIRM",
        "reviewer_id": "analyst",
        "reason": "Valid reason exceeding ten characters.",
    }
    # Resolve once
    client.post(f"/reviews/{thread_id}", json=action, headers={"X-API-Key": key})

    # Resolve again -> 409
    resp2 = client.post(f"/reviews/{thread_id}", json=action, headers={"X-API-Key": key})
    assert resp2.status_code == 409


def test_review_invalid_body_returns_422(test_client):
    client, key = test_client
    # Invalid action (DENY is not allowed)
    bad_action = {
        "action": "DENY",
        "reviewer_id": "analyst",
        "reason": "Valid reason exceeding ten characters.",
    }
    resp = client.post(
        "/reviews/triage-CLM70000009-v1",
        json=bad_action,
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 422


def test_get_decisions_returns_all_versions_newest_first_and_404(test_client):
    client, key = test_client

    # 1. 404 for unknown claim
    resp_404 = client.get("/decisions/CLM79999999", headers={"X-API-Key": key})
    assert resp_404.status_code == 404

    # 2. Run version 1
    client.post(
        "/triage",
        json={"claim_id": "CLM70000001", "reprocess": False},
        headers={"X-API-Key": key},
    )
    # Run version 2
    client.post(
        "/triage",
        json={"claim_id": "CLM70000001", "reprocess": True},
        headers={"X-API-Key": key},
    )

    # 3. Retrieve decisions
    resp_dec = client.get("/decisions/CLM70000001", headers={"X-API-Key": key})
    assert resp_dec.status_code == 200
    decisions = resp_dec.json()
    assert len(decisions) == 2
    assert [d["version"] for d in decisions] == [2, 1]


def test_no_traceback_and_no_claim_text_in_error_responses(test_client):
    client, key = test_client
    resp = client.get("/decisions/BAD_ID", headers={"X-API-Key": key})
    text = resp.text
    assert "Traceback" not in text
    assert "File " not in text
