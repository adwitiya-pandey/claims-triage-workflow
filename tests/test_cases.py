"""Tests for scenario cases, tools interface, and fixture backend per T4 & SPEC 17."""

import json
from collections import Counter
from pathlib import Path

from triage.models import Assessment, Extraction
from triage.retrieval import load_clauses
from triage.tools.base import ClaimsTools
from triage.tools.fixture_backend import FixtureToolsBackend

CASES_DIR = Path("data/cases")


def test_case_counts_and_categories_match_spec_17():
    case_files = list(CASES_DIR.glob("*.json"))
    assert len(case_files) == 43, f"Expected 43 cases, found {len(case_files)}"

    categories = Counter()
    for cf in case_files:
        with open(cf, "r", encoding="utf-8") as f:
            data = json.load(f)
        categories[data["category"]] += 1

    expected_categories = {
        "clean (all checks pass)": 8,
        "preauth missing": 3,
        "not covered": 2,
        "coverage rule unknown": 2,
        "out of network": 2,
        "billed above max allowed": 2,
        "high amount": 2,
        "duplicate (1 pending, 1 approved, 1 paid)": 3,
        "late filing": 2,
        "annual limit exhausted": 2,
        "missing documents": 2,
        "extracted fields differ from record": 3,
        "injection in the submission text": 3,
        "injection in a claim note (from A)": 2,
        "obfuscated injection (hidden characters)": 1,
        "status already PAID": 1,
        "garbled or unreadable submission": 2,
        "claim not in the system": 1,
    }

    assert dict(categories) == expected_categories


def test_gold_extractions_and_assessments_validate():
    clauses = {c.chunk_id: c.text for c in load_clauses()}

    for cf in sorted(CASES_DIR.glob("*.json")):
        with open(cf, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Validate extraction against Pydantic model
        gold_ex = data["gold_extraction"]
        ex = Extraction(**gold_ex)
        assert ex.claim_id == data["claim_id"]

        # Validate assessment
        gold_ass = data["gold_assessment"]
        ass = Assessment(**gold_ass)

        # Verify all cited quotes exist verbatim in the policy corpus
        for item in ass.clauses:
            assert item.chunk_id in clauses, f"Unknown clause {item.chunk_id} in {data['case_id']}"
            corpus_text = clauses[item.chunk_id]
            # Exact or normalized quote verification
            assert item.quote in corpus_text, (
                f"Quote '{item.quote}' not found verbatim in {item.chunk_id} for case {data['case_id']}"
            )


def test_fixture_backend_output_shapes():
    backend = FixtureToolsBackend(CASES_DIR)

    # 1. get_claim
    claim = backend.get_claim("CLM70000001")
    assert claim["claim_id"] == "CLM70000001"
    assert claim["member_id"] == "M700001"
    assert "provider_network_status" in claim
    assert "plan_id" in claim
    assert "billed_amount" in claim

    # 2. list_member_claims
    member_claims = backend.list_member_claims("M700001")
    assert "total" in member_claims
    assert "items" in member_claims
    assert isinstance(member_claims["items"], list)
    assert len(member_claims["items"]) >= 1

    # 3. get_coverage_rule
    rule = backend.get_coverage_rule("PLN001", "PX0001")
    assert rule["plan_id"] == "PLN001"
    assert rule["procedure_code"] == "PX0001"
    assert isinstance(rule["covered"], bool)
    assert isinstance(rule["requires_preauth"], bool)

    # 4. get_member_ytd_summary
    ytd = backend.get_member_ytd_summary("M700001", 2026)
    assert ytd["member_id"] == "M700001"
    assert "remaining_annual_limit" in ytd
    assert "annual_limit" in ytd
    assert isinstance(ytd["by_status"], list)

    # 5. get_claim_notes
    notes = backend.get_claim_notes("CLM70000001")
    assert notes["claim_id"] == "CLM70000001"
    assert notes["untrusted"] is True
    assert isinstance(notes["notes"], list)


def test_tools_interface_has_no_write_method():
    """Security requirement S4: verify no write methods exist on protocol or backend."""
    forbidden_verbs = {
        "write",
        "insert",
        "update",
        "delete",
        "post",
        "put",
        "create",
        "save",
        "set",
        "modify",
        "drop",
    }

    for cls in [ClaimsTools, FixtureToolsBackend]:
        methods = [m for m in dir(cls) if not m.startswith("_")]
        for m in methods:
            m_lower = m.lower()
            for verb in forbidden_verbs:
                assert verb not in m_lower, (
                    f"Forbidden write verb '{verb}' found in method '{m}' on {cls.__name__}"
                )
