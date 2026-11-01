"""Normalized dense embeddings with deterministic offline and hashed caching."""

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np

from triage.llm import _is_transient_error, create_gemini_client, embed_content
from triage.sanitize import sanitize_text

DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
DEFAULT_EMBEDDING_DIMENSION = 768
DEFAULT_CACHE_PATH = Path("data/embeddings/cache.jsonl")
RETRY_DELAYS = (0.0, 1.0, 2.0, 4.0)


class Embedder(Protocol):
    """Interface implemented by production and deterministic test embedders."""

    model: str

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        """Return one normalized vector per document."""

    def embed_query(self, text: str) -> np.ndarray:
        """Return a normalized vector for a query."""


class EmbeddingCacheMiss(Exception):
    """Raised when an offline replay has no vector for a hashed input."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"Embedding cache miss for key '{key}'. Replay is offline.")


def normalize_vectors(vectors: np.ndarray) -> np.ndarray:
    """L2-normalize rows while keeping zero vectors finite and deterministic."""
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2:
        raise ValueError("Embedding batch must be a two-dimensional array")
    if not np.isfinite(matrix).all():
        raise ValueError("Embedding vectors must contain only finite values")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms != 0)


def normalize_vector(vector: np.ndarray) -> np.ndarray:
    """Normalize one vector and validate its shape."""
    array = np.asarray(vector, dtype=np.float32)
    if array.ndim != 1:
        raise ValueError("A query embedding must be one-dimensional")
    return normalize_vectors(array.reshape(1, -1))[0]


class GeminiEmbedder:
    """Gemini retrieval embedder using separate document and query task types."""

    model: str

    def __init__(
        self,
        model: str | None = None,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        dimension: int = DEFAULT_EMBEDDING_DIMENSION,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model or os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
        if not self.model.strip():
            raise ValueError("EMBEDDING_MODEL must not be empty")
        if dimension < 1:
            raise ValueError("Embedding dimension must be positive")
        self.api_key = api_key
        self.client = client
        self.dimension = dimension
        self.sleep_fn = sleep_fn

    def _get_client(self) -> Any:
        if self.client is None:
            api_key = self.api_key
            if api_key is None:
                from triage.config import TriageConfig

                api_key = TriageConfig.load().GEMINI_API_KEY
            if not api_key.strip():
                raise ValueError("GEMINI_API_KEY is required for Gemini embeddings")
            self.client = create_gemini_client(api_key)
        return self.client

    def _embed(self, contents: str | list[str], task_type: str) -> np.ndarray:
        client = self._get_client()
        response = None
        for attempt, delay in enumerate(RETRY_DELAYS):
            if delay:
                self.sleep_fn(delay)
            try:
                response = embed_content(
                    client,
                    model=self.model,
                    contents=contents,
                    task_type=task_type,
                    output_dimensionality=self.dimension,
                )
            except Exception as error:
                if attempt == len(RETRY_DELAYS) - 1 or not _is_transient_error(error):
                    raise
        if response is None:
            raise RuntimeError("Embedding request returned no result")
        embeddings = getattr(response, "embeddings", None)
        if embeddings is None:
            raise ValueError("Gemini response did not contain embeddings")
        values = [getattr(embedding, "values", None) for embedding in embeddings]
        if not values or any(vector is None for vector in values):
            raise ValueError("Gemini response contained an empty embedding")
        matrix = normalize_vectors(np.asarray(values, dtype=np.float32))
        if matrix.shape[1] != self.dimension:
            raise ValueError(
                f"Gemini returned {matrix.shape[1]} dimensions; "
                f"expected {self.dimension}"
            )
        expected_rows = 1 if isinstance(contents, str) else len(contents)
        if matrix.shape[0] != expected_rows:
            raise ValueError(
                f"Gemini returned {matrix.shape[0]} embeddings; "
                f"expected {expected_rows}"
            )
        return matrix

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        """Embed policy documents in bounded batches using retrieval-document mode."""
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)
        batch_size = 100
        batches = [
            self._embed(texts[start : start + batch_size], "RETRIEVAL_DOCUMENT")
            for start in range(0, len(texts), batch_size)
        ]
        return normalize_vectors(np.vstack(batches))

    def embed_query(self, text: str) -> np.ndarray:
        """Sanitize and embed a query using retrieval-query mode."""
        clean_text, _ = sanitize_text(text)
        return self._embed(clean_text, "RETRIEVAL_QUERY")[0]


class HashEmbedder:
    """Stable bag-of-hashed-tokens embedder for tests and offline experiments."""

    model = "hash-embedder-v1"

    def __init__(self, dimension: int = DEFAULT_EMBEDDING_DIMENSION) -> None:
        if dimension < 1:
            raise ValueError("Embedding dimension must be positive")
        self.dimension = dimension

    def _embed_one(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dimension, dtype=np.float32)
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimension
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[index] += sign
        return normalize_vector(vector)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)
        return normalize_vectors(np.vstack([self._embed_one(text) for text in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        clean_text, _ = sanitize_text(text)
        return self._embed_one(clean_text)


class CachedEmbedder:
    """Persist vectors by hashed model/task/dimension/text without storing text."""

    def __init__(
        self,
        embedder: Embedder,
        *,
        cache_path: str | Path = DEFAULT_CACHE_PATH,
        dimension: int = DEFAULT_EMBEDDING_DIMENSION,
        replay: bool = False,
    ) -> None:
        if dimension < 1:
            raise ValueError("Embedding dimension must be positive")
        self.embedder = embedder
        self.model = embedder.model
        self.cache_path = Path(cache_path)
        self.dimension = dimension
        self.replay = replay
        self._cache: dict[str, np.ndarray] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        if not self.cache_path.exists():
            return
        for line_number, line in enumerate(
            self.cache_path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            entry = json.loads(line)
            if set(entry) != {"key", "vector"}:
                raise ValueError(f"Invalid embedding cache entry at line {line_number}")
            vector = normalize_vector(np.asarray(entry["vector"], dtype=np.float32))
            if vector.size != self.dimension:
                raise ValueError(
                    f"Embedding cache dimension mismatch at line {line_number}"
                )
            self._cache[entry["key"]] = vector

    def _key(self, task_type: str, text: str) -> str:
        canonical = json.dumps(
            {
                "model": self.model,
                "task_type": task_type,
                "dimension": self.dimension,
                "text": text,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _save(self, key: str, vector: np.ndarray) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"key": key, "vector": vector.tolist()}
        with self.cache_path.open("a", encoding="utf-8") as cache_file:
            cache_file.write(json.dumps(entry, separators=(",", ":")) + "\n")
        self._cache[key] = vector

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)
        keys = [self._key("RETRIEVAL_DOCUMENT", text) for text in texts]
        missing: dict[str, str] = {}
        for key, text in zip(keys, texts, strict=True):
            if key not in self._cache:
                missing.setdefault(key, text)
        if missing:
            if self.replay:
                raise EmbeddingCacheMiss(next(iter(missing)))
            missing_texts = list(missing.values())
            vectors = normalize_vectors(self.embedder.embed_documents(missing_texts))
            if vectors.shape != (len(missing_texts), self.dimension):
                raise ValueError(
                    "Embedder returned an unexpected document vector shape"
                )
            for key, vector in zip(missing, vectors, strict=True):
                self._save(key, vector)
        return np.vstack([self._cache[key] for key in keys])

    def embed_query(self, text: str) -> np.ndarray:
        clean_text, _ = sanitize_text(text)
        key = self._key("RETRIEVAL_QUERY", clean_text)
        if key in self._cache:
            return self._cache[key].copy()
        if self.replay:
            raise EmbeddingCacheMiss(key)
        vector = normalize_vector(self.embedder.embed_query(clean_text))
        if vector.size != self.dimension:
            raise ValueError("Embedder returned an unexpected query vector shape")
        self._save(key, vector)
        return vector.copy()
