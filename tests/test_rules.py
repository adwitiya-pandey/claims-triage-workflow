"""Tests for pure rule engine per SPEC 11."""

from triage.rules import evaluate_rules, route

BASE_CLEAN_FACTS = {
    "confidence": 0.95,
    "claim": {
        "claim_id": "CLM70000001",
        "member_id": "M700001",
        "provider_id": "PRV0001",
        "provider_network_status": "IN_NETWORK",
        "procedure_code": "PX0001",
        "service_date": "2026-05-10",
        "submitted_on": "2026-05-20",
        "billed_amount": 150.0,
        "status": "SUBMITTED",
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
    },
    "coverage_rule": {
        "covered": True,
        "requires_preauth": False,
        "max_allowed": 500.0,
    },
    "required_documents": ["claim_form", "itemized_bill"],
    "remaining_annual_limit": 5000.0,
    "duplicates": [],
    "supports": [{"chunk_id": "POL-PX0001", "quote": "Routine general medical consultations"}],
    "restricts": [],
    "injection_flags": [],
    "claim_missing": False,
    "extraction_failed": False,
    "tool_error": None,
}


def test_clean_claim_routes_fast_track():
    rt, ids = route(BASE_CLEAN_FACTS)
    assert rt == "FAST_TRACK"
    assert ids == []


def test_not_covered_routes_standard_review():
    f = {
        **BASE_CLEAN_FACTS,
        "coverage_rule": {"covered": False, "requires_preauth": False, "max_allowed": None},
    }
    rt, ids = route(f)
    assert rt == "STANDARD_REVIEW"
    assert "R03" in ids


def test_coverage_rule_unknown_routes_escalate():
    f = {**BASE_CLEAN_FACTS, "coverage_rule": None}
    rt, ids = route(f)
    assert rt == "ESCALATE"
    assert "R02" in ids


def test_preauth_missing_and_present():
    # Requires preauth, but none provided
    f_missing = {
        **BASE_CLEAN_FACTS,
        "coverage_rule": {"covered": True, "requires_preauth": True, "max_allowed": 500.0},
        "extracted": {**BASE_CLEAN_FACTS["extracted"], "preauth_reference": None},
    }
    rt, ids = route(f_missing)
    assert rt == "STANDARD_REVIEW"
    assert "R04" in ids

    # Requires preauth, provided
    f_present = {
        **BASE_CLEAN_FACTS,
        "coverage_rule": {"covered": True, "requires_preauth": True, "max_allowed": 500.0},
        "extracted": {**BASE_CLEAN_FACTS["extracted"], "preauth_reference": "PA-1234"},
    }
    rt2, ids2 = route(f_present)
    assert rt2 == "FAST_TRACK"
    assert ids2 == []


def test_billed_over_max_allowed():
    f = {
        **BASE_CLEAN_FACTS,
        "coverage_rule": {"covered": True, "requires_preauth": False, "max_allowed": 100.0},
    }
    rt, ids = route(f)
    assert rt == "STANDARD_REVIEW"
    assert "R05" in ids


def test_high_amount_escalates():
    f = {
        **BASE_CLEAN_FACTS,
        "claim": {**BASE_CLEAN_FACTS["claim"], "billed_amount": 12000.0},
        "extracted": {**BASE_CLEAN_FACTS["extracted"], "billed_amount": 12000.0},
    }
    rt, ids = route(f)
    assert rt == "ESCALATE"
    assert "R06" in ids


def test_out_of_network_routes_standard_review():
    f = {
        **BASE_CLEAN_FACTS,
        "claim": {**BASE_CLEAN_FACTS["claim"], "provider_network_status": "OUT_OF_NETWORK"},
    }
    rt, ids = route(f)
    assert rt == "STANDARD_REVIEW"
    assert "R07" in ids


def test_duplicates():
    # Pending duplicate
    f_pending = {**BASE_CLEAN_FACTS, "duplicates": [{"status": "PENDED"}]}
    rt, ids = route(f_pending)
    assert rt == "STANDARD_REVIEW"
    assert "R08" in ids

    # Approved duplicate
    f_app = {**BASE_CLEAN_FACTS, "duplicates": [{"status": "APPROVED"}]}
    rt_app, ids_app = route(f_app)
    assert rt_app == "ESCALATE"
    assert "R08" in ids_app

    # Paid duplicate
    f_paid = {**BASE_CLEAN_FACTS, "duplicates": [{"status": "PAID"}]}
    rt_paid, ids_paid = route(f_paid)
    assert rt_paid == "ESCALATE"
    assert "R08" in ids_paid


def test_annual_limit_exhausted():
    f = {**BASE_CLEAN_FACTS, "remaining_annual_limit": 50.0}  # billed is 150
    rt, ids = route(f)
    assert rt == "STANDARD_REVIEW"
    assert "R09" in ids


def test_timely_filing_90_days():
    # Exactly 90 days -> FAST_TRACK
    f_90 = {
        **BASE_CLEAN_FACTS,
        "claim": {
            **BASE_CLEAN_FACTS["claim"],
            "service_date": "2026-01-01",
            "submitted_on": "2026-04-01",
        },
        "extracted": {**BASE_CLEAN_FACTS["extracted"], "service_date": "2026-01-01"},
    }
    rt, ids = route(f_90)
    assert rt == "FAST_TRACK"
    assert ids == []

    # 91 days -> STANDARD_REVIEW
    f_91 = {
        **BASE_CLEAN_FACTS,
        "claim": {
            **BASE_CLEAN_FACTS["claim"],
            "service_date": "2026-01-01",
            "submitted_on": "2026-04-02",
        },
        "extracted": {**BASE_CLEAN_FACTS["extracted"], "service_date": "2026-01-01"},
    }
    rt2, ids2 = route(f_91)
    assert rt2 == "STANDARD_REVIEW"
    assert "R11" in ids2


def test_missing_required_documents():
    f = {**BASE_CLEAN_FACTS, "required_documents": ["claim_form", "itemized_bill", "referral"]}
    rt, ids = route(f)
    assert rt == "STANDARD_REVIEW"
    assert "R14" in ids


def test_discrepancies_escalate():
    # Amount discrepancy
    f_amt = {
        **BASE_CLEAN_FACTS,
        "extracted": {**BASE_CLEAN_FACTS["extracted"], "billed_amount": 200.0},
    }
    assert route(f_amt)[0] == "ESCALATE"
    assert "R10" in route(f_amt)[1]

    # Member discrepancy
    f_mem = {
        **BASE_CLEAN_FACTS,
        "extracted": {**BASE_CLEAN_FACTS["extracted"], "member_id": "M999999"},
    }
    assert route(f_mem)[0] == "ESCALATE"
    assert "R10" in route(f_mem)[1]


def test_claim_status_not_submitted_or_pended():
    f = {**BASE_CLEAN_FACTS, "claim": {**BASE_CLEAN_FACTS["claim"], "status": "PAID"}}
    rt, ids = route(f)
    assert rt == "ESCALATE"
    assert "R16" in ids


def test_injection_flag_escalates():
    f = {**BASE_CLEAN_FACTS, "injection_flags": ["PATTERN"]}
    rt, ids = route(f)
    assert rt == "ESCALATE"
    assert "R12" in ids


def test_extraction_failure_and_low_confidence():
    f_fail = {**BASE_CLEAN_FACTS, "extraction_failed": True}
    assert route(f_fail) == ("ESCALATE", ["R13"])

    f_low = {**BASE_CLEAN_FACTS, "confidence": 0.5}
    assert route(f_low) == ("ESCALATE", ["R13"])


def test_claim_missing_and_tool_error_early_exit():
    # Claim missing
    f_miss = {**BASE_CLEAN_FACTS, "claim_missing": True}
    assert route(f_miss) == ("ESCALATE", ["R01"])

    # Tool error
    f_tool = {**BASE_CLEAN_FACTS, "tool_error": "INTERNAL"}
    assert route(f_tool) == ("ESCALATE", ["R18"])

    # Tool error + injection: both reported
    f_both = {**BASE_CLEAN_FACTS, "tool_error": "INTERNAL", "injection_flags": ["HIDDEN_CHARS"]}
    rt, ids = route(f_both)
    assert rt == "ESCALATE"
    assert "R12" in ids
    assert "R18" in ids


def test_citation_checks_supports_and_restricts():
    # No supporting clause -> R15 ESCALATE
    f_no_sup = {**BASE_CLEAN_FACTS, "supports": []}
    rt, ids = route(f_no_sup)
    assert rt == "ESCALATE"
    assert "R15" in ids

    # Restricting clause -> R17 STANDARD_REVIEW
    f_restr = {
        **BASE_CLEAN_FACTS,
        "restricts": [{"chunk_id": "POL-EXC-01", "quote": "Experimental therapies"}],
    }
    rt2, ids2 = route(f_restr)
    assert rt2 == "STANDARD_REVIEW"
    assert "R17" in ids2


def test_combinations_and_severity_properties():
    # Two medium hits -> STANDARD_REVIEW
    f_two_med = {
        **BASE_CLEAN_FACTS,
        "coverage_rule": {"covered": False, "requires_preauth": False, "max_allowed": None},  # R03
        "claim": {**BASE_CLEAN_FACTS["claim"], "provider_network_status": "OUT_OF_NETWORK"},  # R07
    }
    rt, ids = route(f_two_med)
    assert rt == "STANDARD_REVIEW"
    assert "R03" in ids
    assert "R07" in ids

    # Medium + injection -> ESCALATE with both
    f_med_inj = {
        **f_two_med,
        "injection_flags": ["PATTERN"],  # R12
    }
    rt2, ids2 = route(f_med_inj)
    assert rt2 == "ESCALATE"
    assert "R03" in ids2
    assert "R07" in ids2
    assert "R12" in ids2


def test_rule_properties():
    # Monotonicity: adding a hit never lowers severity
    rank = {"FAST_TRACK": 0, "STANDARD_REVIEW": 1, "ESCALATE": 2}
    rt_base, _ = route(BASE_CLEAN_FACTS)
    assert rt_base == "FAST_TRACK"

    # Any modification with an issue cannot lower below FAST_TRACK
    f_out = {
        **BASE_CLEAN_FACTS,
        "claim": {**BASE_CLEAN_FACTS["claim"], "provider_network_status": "OUT_OF_NETWORK"},
    }
    rt_out, _ = route(f_out)
    assert rank[rt_out] >= rank[rt_base]

    f_inj = {**f_out, "injection_flags": ["PATTERN"]}
    rt_inj, _ = route(f_inj)
    assert rank[rt_inj] >= rank[rt_out]

    # Only three possible routes
    allowed_routes = {"FAST_TRACK", "STANDARD_REVIEW", "ESCALATE"}
    assert rt_base in allowed_routes
    assert rt_out in allowed_routes
    assert rt_inj in allowed_routes


def test_evaluate_rules_returns_rule_hits():
    rt, hits = evaluate_rules(BASE_CLEAN_FACTS)
    assert rt == "FAST_TRACK"
    assert hits == []

    f_inj = {**BASE_CLEAN_FACTS, "injection_flags": ["PATTERN"]}
    rt2, hits2 = evaluate_rules(f_inj)
    assert rt2 == "ESCALATE"
    assert len(hits2) == 1
    assert hits2[0].rule_id == "R12"
    assert hits2[0].route == "ESCALATE"
