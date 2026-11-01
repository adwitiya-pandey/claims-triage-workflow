"""Tests for exact NumPy cosine search and corpus-versioned persistence."""

import numpy as np
import pytest

from scripts.build_index import build_index
from triage.embeddings import HashEmbedder
from triage.retrieval import compute_corpus_sha256, load_corpus
from triage.vectorstore import NumpyVectorStore


def test_numpy_store_uses_cosine_and_breaks_ties_by_chunk_id():
    store = NumpyVectorStore(
        chunk_ids=["POL-Z", "POL-A", "POL-B"],
        vectors=np.array([[1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]),
        model="hash-v1",
        corpus_sha256="corpus-hash",
    )

    results = store.search(np.array([0.0, 2.0]), top_k=3)

    assert results == [
        ("POL-A", pytest.approx(1.0)),
        ("POL-B", pytest.approx(1.0)),
        ("POL-Z", pytest.approx(0.0)),
    ]


def test_numpy_store_round_trips_index_and_manifest(tmp_path):
    index_path = tmp_path / "index.npz"
    store = NumpyVectorStore(
        chunk_ids=["POL-A", "POL-B"],
        vectors=np.array([[1.0, 0.0], [0.0, 1.0]]),
        model="test-model",
        corpus_sha256="corpus-hash",
    )

    store.save(index_path)
    restored = NumpyVectorStore.load(
        index_path,
        expected_corpus_sha256="corpus-hash",
        expected_model="test-model",
    )

    assert restored.chunk_ids == ["POL-A", "POL-B"]
    assert np.array_equal(restored.vectors, store.vectors)
    assert (tmp_path / "manifest.json").is_file()


def test_numpy_store_refuses_a_different_corpus(tmp_path):
    index_path = tmp_path / "index.npz"
    NumpyVectorStore(
        chunk_ids=["POL-A"],
        vectors=np.array([[1.0, 0.0]]),
        model="test-model",
        corpus_sha256="old-corpus",
    ).save(index_path)

    with pytest.raises(ValueError, match="corpus_sha256"):
        NumpyVectorStore.load(
            index_path,
            expected_corpus_sha256="new-corpus",
        )


def test_build_index_embeds_non_procedure_clauses_offline(tmp_path):
    corpus = load_corpus("data/policy/clauses.md")
    index_path = tmp_path / "index.npz"

    store = build_index(
        index_path=index_path,
        cache_path=tmp_path / "cache.jsonl",
        embedder=HashEmbedder(dimension=16),
    )

    assert store.chunk_ids
    assert all(not chunk_id.startswith("POL-PX") for chunk_id in store.chunk_ids)
    assert store.corpus_sha256 == compute_corpus_sha256(corpus)
    assert (
        NumpyVectorStore.load(
            index_path,
            expected_corpus_sha256=compute_corpus_sha256(corpus),
        ).dimension
        == 16
    )
