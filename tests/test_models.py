"""Tests for models and ID validation."""

import pytest
from pydantic import ValidationError

from triage.models import (
    Assessment,
    ClauseItem,
    Extraction,
    ReviewAction,
    RuleHit,
    TrailEntry,
    validate_claim_id,
    validate_clause_id,
    validate_member_id,
    validate_plan_id,
    validate_procedure_code,
    validate_provider_id,
    validate_reviewer_id,
)


def test_claim_id_validation():
    # Valid
    assert validate_claim_id("CLM12345678") == "CLM12345678"
    assert validate_claim_id("CLM00000000") == "CLM00000000"

    # Invalid: trailing newline
    with pytest.raises(ValidationError):
        validate_claim_id("CLM12345678\n")

    # Invalid: non-ASCII digits (e.g. Arabic-Indic digit ٥ U+0665)
    with pytest.raises(ValidationError):
        validate_claim_id("CLM1234567٥")

    # Invalid: empty
    with pytest.raises(ValidationError):
        validate_claim_id("")

    # Invalid: overlong / short
    with pytest.raises(ValidationError):
        validate_claim_id("CLM123456789")
    with pytest.raises(ValidationError):
        validate_claim_id("CLM1234567")

    # Invalid: wrong prefix or format
    with pytest.raises(ValidationError):
        validate_claim_id("claim12345678")
    with pytest.raises(ValidationError):
        validate_claim_id("CLMabcdefgh")


def test_member_id_validation():
    assert validate_member_id("M123456") == "M123456"
    with pytest.raises(ValidationError):
        validate_member_id("M123456\n")
    with pytest.raises(ValidationError):
        validate_member_id("M12345")
    with pytest.raises(ValidationError):
        validate_member_id("M1234567")
    with pytest.raises(ValidationError):
        validate_member_id("M12345٥")


def test_plan_id_validation():
    assert validate_plan_id("PLN123") == "PLN123"
    with pytest.raises(ValidationError):
        validate_plan_id("PLN123\n")
    with pytest.raises(ValidationError):
        validate_plan_id("PLN12")
    with pytest.raises(ValidationError):
        validate_plan_id("PLN1234")


def test_provider_id_validation():
    assert validate_provider_id("PRV1234") == "PRV1234"
    with pytest.raises(ValidationError):
        validate_provider_id("PRV1234\n")
    with pytest.raises(ValidationError):
        validate_provider_id("PRV123")
    with pytest.raises(ValidationError):
        validate_provider_id("PRV12345")


def test_procedure_code_validation():
    assert validate_procedure_code("PX1234") == "PX1234"
    with pytest.raises(ValidationError):
        validate_procedure_code("PX1234\n")
    with pytest.raises(ValidationError):
        validate_procedure_code("PX123")
    with pytest.raises(ValidationError):
        validate_procedure_code("PX12345")


def test_clause_id_validation():
    assert validate_clause_id("POL-PX0001") == "POL-PX0001"
    assert validate_clause_id("POL-PREAUTH-01") == "POL-PREAUTH-01"
    with pytest.raises(ValidationError):
        validate_clause_id("POL-PX0001\n")
    with pytest.raises(ValidationError):
        validate_clause_id("POL-1")  # too short
    with pytest.raises(ValidationError):
        validate_clause_id("POL-" + "A" * 25)  # too long
    with pytest.raises(ValidationError):
        validate_clause_id("pol-px0001")  # lowercase invalid


def test_reviewer_id_validation():
    assert validate_reviewer_id("analyst.one") == "analyst.one"
    assert validate_reviewer_id("rev_01") == "rev_01"
    assert validate_reviewer_id("dr-smith") == "dr-smith"
    with pytest.raises(ValidationError):
        validate_reviewer_id("analyst.one\n")
    with pytest.raises(ValidationError):
        validate_reviewer_id("1reviewer")  # must start with [a-z]
    with pytest.raises(ValidationError):
        validate_reviewer_id("ab")  # min 3 chars
    with pytest.raises(ValidationError):
        validate_reviewer_id("a" * 33)  # max 32 chars


def test_extraction_validation():
    valid_data = {
        "claim_id": "CLM12345678",
        "member_id": "M123456",
        "provider_id": "PRV1234",
        "procedure_code": "PX0001",
        "procedure_description": "Standard Consultation",
        "billed_amount": 150.00,
        "service_date": "2026-05-10",
        "preauth_reference": "PA-9988",
        "documents_present": ["claim_form", "itemized_bill"],
        "confidence": 0.95,
        "unreadable_fields": [],
    }
    ex = Extraction(**valid_data)
    assert ex.billed_amount == 150.00
    assert ex.procedure_code == "PX0001"

    # Extra fields forbidden
    with pytest.raises(ValidationError):
        Extraction(**{**valid_data, "extra_field": "hacked"})

    # Negative amount forbidden
    with pytest.raises(ValidationError):
        Extraction(**{**valid_data, "billed_amount": -10.0})

    # Confidence out of bounds
    with pytest.raises(ValidationError):
        Extraction(**{**valid_data, "confidence": 1.5})
    with pytest.raises(ValidationError):
        Extraction(**{**valid_data, "confidence": -0.1})

    # Invalid document present
    with pytest.raises(ValidationError):
        Extraction(**{**valid_data, "documents_present": ["random_doc"]})


def test_assessment_validation():
    valid_data = {
        "summary": "Policy assessment summary.",
        "clauses": [
            {
                "chunk_id": "POL-PX0001",
                "effect": "supports",
                "quote": "Verbatim quote from policy.",
            }
        ],
        "missing_items": ["referral"],
        "confidence": 0.9,
    }
    ass = Assessment(**valid_data)
    assert ass.summary == "Policy assessment summary."

    # Extra fields forbidden
    with pytest.raises(ValidationError):
        Assessment(**{**valid_data, "extra": "forbidden"})

    # Overlong summary > 700 chars
    with pytest.raises(ValidationError):
        Assessment(**{**valid_data, "summary": "x" * 701})

    # Invalid clause effect
    with pytest.raises(ValidationError):
        Assessment(
            **{
                **valid_data,
                "clauses": [{"chunk_id": "POL-PX0001", "effect": "invalid", "quote": "quote"}],
            }
        )

    # Overlong quote > 300 chars
    with pytest.raises(ValidationError):
        ClauseItem(chunk_id="POL-PX0001", effect="supports", quote="q" * 301)


def test_review_action_validation():
    valid = {
        "reviewer_id": "analyst.one",
        "action": "CONFIRM",
        "reason": "Reviewed and confirmed all policy requirements are met.",
    }
    act = ReviewAction(**valid)
    assert act.action == "CONFIRM"

    # ESCALATE_FURTHER is valid
    act2 = ReviewAction(**{**valid, "action": "ESCALATE_FURTHER"})
    assert act2.action == "ESCALATE_FURTHER"

    # S9 & SPEC 7: DENY, APPROVE, PAY are forbidden and do not exist
    for forbidden in ["DENY", "APPROVE", "PAY", "REJECT", "ACCEPT"]:
        with pytest.raises(ValidationError):
            ReviewAction(**{**valid, "action": forbidden})

    # Reason too short (< 10 chars)
    with pytest.raises(ValidationError):
        ReviewAction(**{**valid, "reason": "Too short"})

    # Reason too long (> 500 chars)
    with pytest.raises(ValidationError):
        ReviewAction(**{**valid, "reason": "x" * 501})

    # Extra fields forbidden
    with pytest.raises(ValidationError):
        ReviewAction(**{**valid, "extra": "no"})


def test_rule_hit_and_trail():
    hit = RuleHit(rule_id="R06", route="STANDARD_REVIEW", evidence=["get_claim.billed_amount"])
    assert hit.rule_id == "R06"
    with pytest.raises(ValidationError):
        RuleHit(rule_id="R06", route="INVALID_ROUTE", evidence=[])

    entry = TrailEntry(
        node="intake",
        started_at="2026-10-19T10:00:00Z",
        ended_at="2026-10-19T10:00:01Z",
        outcome="ok",
        detail_codes=["CLEAN"],
    )
    assert entry.node == "intake"
