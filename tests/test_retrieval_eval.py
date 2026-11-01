"""Tests for retrieval relevance metrics and the synthetic gold set."""

from evals.retrieval_eval import (
    _build_runtime_query,
    calculate_metrics,
    load_gold_set,
)


def test_retrieval_metrics_with_binary_relevance():
    metrics = calculate_metrics(
        relevant_chunk_ids={"A", "C"},
        ranked_chunk_ids=["B", "A", "C", "D"],
    )

    assert metrics == {
        "recall@1": 0.0,
        "recall@3": 1.0,
        "recall@4": 1.0,
        "mrr": 0.5,
        "ndcg@4": 0.6934264036172708,
    }


def test_retrieval_metrics_empty_relevance_is_zero():
    metrics = calculate_metrics(set(), ["A", "B"])

    assert metrics == {
        "recall@1": 0.0,
        "recall@3": 0.0,
        "recall@4": 0.0,
        "mrr": 0.0,
        "ndcg@4": 0.0,
    }


def test_gold_set_covers_all_labeled_cases_without_claim_text():
    records = load_gold_set()

    assert len(records) == 43
    assert all(record["query_text"] == record["procedure_code"] for record in records)
    assert all(record["relevant_chunk_ids"] for record in records)
    assert all("submission" not in record for record in records)
    assert _build_runtime_query(records[0]).endswith(records[0]["procedure_code"])
