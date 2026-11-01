"""Tests for dense and hybrid ranking while pinning the procedure clause."""

import numpy as np

from triage.embeddings import HashEmbedder
from triage.retrieval import (
    PolicyClause,
    reciprocal_rank_fusion,
    retrieve_with_trace,
)
from triage.vectorstore import NumpyVectorStore


def _toy_clauses():
    return [
        PolicyClause("POL-A", "coverage", None, "knee joint surgery"),
        PolicyClause("POL-B", "process", None, "prior authorization required"),
        PolicyClause("POL-PX0001", "procedure", "PX0001", "office consultation"),
        PolicyClause("POL-PX0002", "procedure", "PX0002", "different procedure"),
    ]


def _store_for(clauses, embedder):
    candidates = [clause for clause in clauses if clause.kind != "procedure"]
    return NumpyVectorStore(
        chunk_ids=[clause.chunk_id for clause in candidates],
        vectors=embedder.embed_documents([clause.text for clause in candidates]),
        model=embedder.model,
        corpus_sha256="toy-corpus",
    )


def test_rrf_matches_hand_computed_scores():
    fused = reciprocal_rank_fusion({"bm25": ["A", "B", "C"], "dense": ["B", "C"]})

    assert fused["A"] == 1 / 61
    assert fused["B"] == 1 / 62 + 1 / 61
    assert fused["C"] == 1 / 63 + 1 / 62


def test_dense_and_hybrid_always_pin_procedure_clause_first():
    clauses = _toy_clauses()
    embedder = HashEmbedder(dimension=32)
    store = _store_for(clauses, embedder)

    for mode in ("bm25", "dense", "hybrid"):
        chunks, trace, actual_mode, flags = retrieve_with_trace(
            query="knee surgery",
            procedure_code="PX0001",
            top_k=2,
            clauses=clauses,
            mode=mode,
            embedder=embedder,
            vector_store=store,
        )

        assert chunks[0]["chunk_id"] == "POL-PX0001"
        assert trace[0]["chunk_id"] == "POL-PX0001"
        assert actual_mode == mode
        assert flags == []
        if mode == "dense":
            assert all(chunk["kind"] != "procedure" for chunk in chunks[1:])


def test_dense_ties_break_by_chunk_id():
    clauses = [
        PolicyClause("POL-Z", "coverage", None, "same words"),
        PolicyClause("POL-A", "coverage", None, "same words"),
        PolicyClause("POL-PX0001", "procedure", "PX0001", "procedure"),
    ]
    embedder = HashEmbedder(dimension=16)
    candidates = [clause for clause in clauses if clause.kind != "procedure"]
    vector = embedder.embed_documents(["same words"])[0]
    store = NumpyVectorStore(
        chunk_ids=[clause.chunk_id for clause in candidates],
        vectors=np.tile(vector, (len(candidates), 1)),
        model=embedder.model,
        corpus_sha256="toy-corpus",
    )

    chunks, _, _, _ = retrieve_with_trace(
        "same words",
        "PX0001",
        top_k=2,
        clauses=clauses,
        mode="dense",
        embedder=embedder,
        vector_store=store,
    )

    assert [chunk["chunk_id"] for chunk in chunks] == [
        "POL-PX0001",
        "POL-A",
        "POL-Z",
    ]


def test_embedder_failure_falls_back_to_bm25():
    class BrokenEmbedder:
        def embed_query(self, text):
            raise RuntimeError("embedding unavailable")

    clauses = _toy_clauses()
    embedder = HashEmbedder(dimension=16)
    store = _store_for(clauses, embedder)

    chunks, _, actual_mode, flags = retrieve_with_trace(
        "knee surgery",
        "PX0001",
        top_k=2,
        clauses=clauses,
        mode="hybrid",
        embedder=BrokenEmbedder(),
        vector_store=store,
    )

    assert chunks[0]["chunk_id"] == "POL-PX0001"
    assert actual_mode == "bm25"
    assert flags == ["dense_unavailable"]
