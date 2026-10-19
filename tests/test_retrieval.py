"""Tests for policy clause parsing and hand-written BM25 retrieval."""

import pytest

from triage.retrieval import (
    BM25Index,
    Clause,
    compute_corpus_sha256,
    load_clauses,
    parse_clauses_text,
    retrieve,
    tokenize,
)


def test_tokenizer():
    text = "The Patient Underwent, Examination! (Standard Routine)."
    tokens = tokenize(text)
    assert tokens == ["patient", "underwent", "examination", "standard", "routine"]
    # Check stop words are filtered
    stop_tokens = tokenize("this is with and or the for an in on at")
    assert stop_tokens == []


def test_toy_corpus_bm25_ranking_and_hand_computed_scores():
    toy_docs = [
        Clause(
            chunk_id="POL-PX0001",
            kind="procedure",
            procedure_code="PX0001",
            text="primary care consultation standard medical check",
        ),
        Clause(
            chunk_id="POL-PREAUTH-01",
            kind="coverage",
            procedure_code=None,
            text="preauthorisation required for elective surgery",
        ),
        Clause(
            chunk_id="POL-TIME-01",
            kind="process",
            procedure_code=None,
            text="timely filing within ninety days for all claims",
        ),
        Clause(
            chunk_id="POL-NET-01",
            kind="coverage",
            procedure_code=None,
            text="network provider negotiated rates for primary care",
        ),
        Clause(
            chunk_id="POL-NET-02",
            kind="coverage",
            procedure_code=None,
            text="out of network provider care reduced reimbursement",
        ),
    ]

    index = BM25Index(toy_docs)
    query_tokens = tokenize("primary care")

    scored = index.score_documents(query_tokens)
    # Both POL-NET-01 and POL-PX0001 have length 6 and match 'primary' & 'care' once,
    # so their BM25 score is identical (1.3929). Tie-break by chunk_id puts POL-NET-01 first.
    assert scored[0][0].chunk_id == "POL-NET-01"
    assert pytest.approx(scored[0][1], abs=1e-3) == 1.3929

    assert scored[1][0].chunk_id == "POL-PX0001"
    assert pytest.approx(scored[1][1], abs=1e-3) == 1.3929

    assert scored[2][0].chunk_id == "POL-NET-02"
    assert pytest.approx(scored[2][1], abs=1e-3) == 0.5308

    # Check tie-break by chunk_id when scores are equal (0.0):
    # POL-PREAUTH-01 < POL-TIME-01 alphabetically
    assert scored[3][0].chunk_id == "POL-PREAUTH-01"
    assert scored[4][0].chunk_id == "POL-TIME-01"
    assert scored[3][1] == 0.0
    assert scored[4][1] == 0.0


def test_procedure_clause_always_returned():
    toy_docs = [
        Clause(
            chunk_id="POL-PX0001",
            kind="procedure",
            procedure_code="PX0001",
            text="consultation standard medical check",
        ),
        Clause(
            chunk_id="POL-PREAUTH-01",
            kind="coverage",
            procedure_code=None,
            text="preauthorisation required for elective surgery",
        ),
        Clause(
            chunk_id="POL-TIME-01",
            kind="process",
            procedure_code=None,
            text="timely filing within ninety days for all claims",
        ),
    ]

    # Query has nothing to do with procedure clause
    results = retrieve(
        query="elective surgery",
        procedure_code="PX0001",
        top_k=1,
        clauses=toy_docs,
    )

    # First result must always be the procedure clause
    assert len(results) == 2
    assert results[0]["chunk_id"] == "POL-PX0001"
    assert results[0]["kind"] == "procedure"
    assert results[1]["chunk_id"] == "POL-PREAUTH-01"


def test_deterministic_output():
    results1 = retrieve(query="routine consultation", procedure_code="PX0001", top_k=4)
    results2 = retrieve(query="routine consultation", procedure_code="PX0001", top_k=4)
    assert results1 == results2


def test_corpus_loader_and_validator():
    clauses = load_clauses()
    assert len(clauses) >= 25

    # Check exactly 12 procedure codes PX0001..PX0012
    proc_codes = {c.procedure_code for c in clauses if c.kind == "procedure"}
    expected_codes = {f"PX{i:04d}" for i in range(1, 13)}
    assert proc_codes == expected_codes

    # Check unique IDs
    ids = [c.chunk_id for c in clauses]
    assert len(ids) == len(set(ids))

    # Check valid kinds
    valid_kinds = {"coverage", "exclusion", "procedure", "documentation", "process"}
    for c in clauses:
        assert c.kind in valid_kinds

    # Check at least 3 exclusions
    exclusions = [c for c in clauses if c.kind == "exclusion"]
    assert len(exclusions) >= 3


def test_loader_errors_on_malformed_clauses():
    # Duplicate ID
    malformed_dup = """## POL-TEST-01
kind: coverage
---
First clause text with enough words to satisfy constraints and details.

## POL-TEST-01
kind: coverage
---
Second clause duplicate ID text with enough words to satisfy constraints.
"""
    with pytest.raises(ValueError, match="Duplicate clause ID"):
        parse_clauses_text(malformed_dup, validate_all_procedures=False)

    # Invalid kind
    malformed_kind = """## POL-TEST-02
kind: invalid_kind
---
Valid words here describing policy text for a test clause.
"""
    with pytest.raises(ValueError, match="Invalid kind"):
        parse_clauses_text(malformed_kind, validate_all_procedures=False)

    # Procedure kind missing procedure_code
    malformed_proc = """## POL-TEST-03
kind: procedure
---
Valid words here describing policy text for a procedure without code.
"""
    with pytest.raises(ValueError, match="procedure_code required"):
        parse_clauses_text(malformed_proc, validate_all_procedures=False)


def test_corpus_sha256():
    h = compute_corpus_sha256()
    assert isinstance(h, str)
    assert len(h) == 64
    assert int(h, 16) > 0  # valid hex
