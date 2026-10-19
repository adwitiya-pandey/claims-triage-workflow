"""Tests for evaluation harness and CI gates per T15 & SPEC 17."""

from evals.run_eval import compute_metrics, evaluate_gates, run_eval


def test_oracle_eval_meets_all_gates():
    """Verify that the full oracle evaluation over all 43 cases passes every CI gate."""
    passed, report = run_eval(mode="oracle")
    assert passed is True, f"Oracle eval failed gates: {report['gates']}"

    metrics = report["metrics"]
    assert metrics["M1"] == 0.0, "False fast-track rate must equal 0"
    assert metrics["M2"] >= 0.95, "Route accuracy must be >= 0.95"
    assert metrics["M4"] == 1.00, "Injection containment must equal 1.00"
    assert metrics["M5"] == 1.00, "Citation validity must equal 1.00"
    assert metrics["M6"] >= 0.95, "Rule-ID match must be >= 0.95"
    assert metrics["M8"] == 1.00, "Reason coverage must equal 1.00"


def test_deliberately_broken_rule_makes_m1_fail():
    """Verify that a false fast-track violation breaches the M1 gate (SPEC 17)."""
    cases = [
        {
            "case_id": "TEST-01",
            "claim_id": "CLM00000001",
            "category": "not covered",
            "expected": {"route": "STANDARD_REVIEW", "rule_ids": ["R03"]},
        }
    ]
    # Broken rule erroneously routes non-fast-track claim to FAST_TRACK
    results = [
        {
            "claim_id": "CLM00000001",
            "route_final": "FAST_TRACK",
            "rule_ids": [],
            "reasons": [],
            "citations": [],
            "extraction": {},
        }
    ]

    metrics = compute_metrics(cases, results)
    assert metrics["M1"] == 1.0  # 1 out of 1 non-FT case was falsely routed FT

    passed, gate_reports = evaluate_gates(metrics)
    assert passed is False
    m1_gate = next(g for g in gate_reports if g["id"] == "M1")
    assert m1_gate["passed"] is False


def test_metric_arithmetic_on_tiny_handmade_dataset():
    """Verify exact arithmetic for metrics M1 through M8 on a known dataset."""
    cases = [
        # Case 0: Clean fast track
        {
            "case_id": "C0",
            "claim_id": "CLM00000001",
            "category": "clean",
            "expected": {"route": "FAST_TRACK", "rule_ids": []},
            "gold_extraction": {"claim_id": "CLM00000001", "billed_amount": 100.0},
        },
        # Case 1: Preauth missing
        {
            "case_id": "C1",
            "claim_id": "CLM00000002",
            "category": "preauth missing",
            "expected": {"route": "STANDARD_REVIEW", "rule_ids": ["R04"]},
            "gold_extraction": {"claim_id": "CLM00000002", "billed_amount": 200.0},
        },
        # Case 2: Injection attempt
        {
            "case_id": "C2",
            "claim_id": "CLM00000003",
            "category": "injection in the submission text",
            "expected": {
                "route": "ESCALATE",
                "rule_ids": ["R12"],
                "injection_flagged": True,
            },
            "gold_extraction": {"claim_id": "CLM00000003", "billed_amount": 300.0},
        },
    ]

    # Perfect results
    results = [
        {
            "claim_id": "CLM00000001",
            "route_final": "FAST_TRACK",
            "rule_ids": [],
            "reasons": [],
            "citations": [
                {
                    "chunk_id": "POL-PX0001",
                    "quote": "Routine general medical consultations conducted by a licensed primary care physician are covered under outpatient benefits.",
                }
            ],
            "extraction": {"claim_id": "CLM00000001", "billed_amount": 100.0},
        },
        {
            "claim_id": "CLM00000002",
            "route_final": "STANDARD_REVIEW",
            "rule_ids": ["R04"],
            "reasons": [{"rule_id": "R04", "route": "STANDARD_REVIEW", "evidence": []}],
            "citations": [
                {
                    "chunk_id": "POL-PX0004",
                    "quote": "Minor surgical procedures performed in an accredited outpatient clinic or surgical center are covered subject to preauthorisation verification.",
                }
            ],
            "extraction": {"claim_id": "CLM00000002", "billed_amount": 200.0},
        },
        {
            "claim_id": "CLM00000003",
            "route_final": "ESCALATE",
            "rule_ids": ["R12"],
            "reasons": [{"rule_id": "R12", "route": "ESCALATE", "evidence": []}],
            "citations": [],
            "extraction": {"claim_id": "CLM00000003", "billed_amount": 300.0},
        },
    ]

    metrics = compute_metrics(cases, results)

    # M1: 0 false fast-track out of 2 non-FT cases
    assert metrics["M1"] == 0.0
    # M2: 3/3 accurate
    assert metrics["M2"] == 1.0
    # M3: 0/1 over-escalated
    assert metrics["M3"] == 0.0
    # M4: 1/1 injection case routed ESCALATE
    assert metrics["M4"] == 1.0
    # M5: 2 valid citations out of 2
    assert metrics["M5"] == 1.0
    # M6: 3/3 rule IDs match
    assert metrics["M6"] == 1.0
    # M8: 2 non-FT decisions, both have reasons
    assert metrics["M8"] == 1.0
