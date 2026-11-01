"""Offline tests for embedding normalization, caching, and query sanitization."""

import json
import socket

import numpy as np
import pytest

from triage.embeddings import (
    CachedEmbedder,
    EmbeddingCacheMiss,
    GeminiEmbedder,
    HashEmbedder,
)


class FakeModels:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def embed_content(self, *, model, contents, config):
        self.calls.append((model, contents, config))
        if isinstance(contents, str):
            contents = [contents]
        return type(
            "Response",
            (),
            {
                "embeddings": [
                    type("Embedding", (), {"values": values})()
                    for values in self.values[: len(contents)]
                ]
            },
        )()


class FakeClient:
    def __init__(self, values):
        self.models = FakeModels(values)


class CountingEmbedder:
    model = "test-model"

    def __init__(self):
        self.document_calls = 0
        self.query_calls = 0

    def embed_documents(self, texts):
        self.document_calls += 1
        return np.tile(np.array([[3.0, 4.0]]), (len(texts), 1))

    def embed_query(self, text):
        self.query_calls += 1
        return np.array([3.0, 4.0])


@pytest.fixture
def block_sockets(monkeypatch):
    def fail_connect(*args, **kwargs):
        pytest.fail("unexpected network socket connection")

    monkeypatch.setattr(socket.socket, "connect", fail_connect)


def test_gemini_embedder_batches_and_normalizes_output():
    client = FakeClient([[3.0, 4.0, 0.0], [0.0, 2.0, 0.0]])
    embedder = GeminiEmbedder(model="gemini-embedding-001", client=client, dimension=3)

    vectors = embedder.embed_documents(["policy one", "policy two"])

    assert vectors.shape == (2, 3)
    assert np.allclose(np.linalg.norm(vectors, axis=1), [1.0, 1.0])
    model, contents, config = client.models.calls[0]
    assert model == "gemini-embedding-001"
    assert contents == ["policy one", "policy two"]
    assert config.task_type == "RETRIEVAL_DOCUMENT"
    assert config.output_dimensionality == 3


def test_gemini_query_is_sanitized_and_uses_query_task():
    client = FakeClient([[3.0, 4.0, 0.0]])
    embedder = GeminiEmbedder(model="gemini-embedding-001", client=client, dimension=3)

    vector = embedder.embed_query("claim\u200b query")

    assert np.isclose(np.linalg.norm(vector), 1.0)
    _, contents, config = client.models.calls[0]
    assert contents == "claim query"
    assert config.task_type == "RETRIEVAL_QUERY"


def test_hash_embedder_is_deterministic_and_normalized():
    embedder = HashEmbedder(dimension=32)

    first = embedder.embed_query("routine physician visit")
    second = embedder.embed_query("routine physician visit")

    assert first.shape == (32,)
    assert np.array_equal(first, second)
    assert np.isclose(np.linalg.norm(first), 1.0)


def test_cached_embedder_hits_without_reembedding_and_never_stores_text(tmp_path):
    base = CountingEmbedder()
    cache_path = tmp_path / "cache.jsonl"
    cached = CachedEmbedder(base, cache_path=cache_path, dimension=2)

    first = cached.embed_query("synthetic query")
    second = cached.embed_query("synthetic query")

    assert np.array_equal(first, second)
    assert base.query_calls == 1
    cache_text = cache_path.read_text(encoding="utf-8")
    assert "synthetic query" not in cache_text
    entry = json.loads(cache_text)
    assert set(entry) == {"key", "vector"}


def test_cached_embedder_replay_miss_fails_closed(tmp_path):
    cached = CachedEmbedder(
        CountingEmbedder(),
        cache_path=tmp_path / "missing.jsonl",
        dimension=2,
        replay=True,
    )

    with pytest.raises(EmbeddingCacheMiss, match="cache miss"):
        cached.embed_query("not cached")


def test_offline_hash_embedder_works_when_sockets_are_blocked(block_sockets):
    vectors = HashEmbedder(dimension=16).embed_documents(
        ["synthetic policy clause", "another policy clause"]
    )

    assert vectors.shape == (2, 16)
