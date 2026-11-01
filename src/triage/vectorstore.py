"""Exact cosine search over persisted NumPy vectors."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import numpy as np

from triage.embeddings import normalize_vector, normalize_vectors


class VectorStore(Protocol):
    """Interface for deterministic nearest-neighbor search."""

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int,
        *,
        exclude_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Return chunk IDs and cosine similarities in deterministic rank order."""


class NumpyVectorStore:
    """In-memory normalized vectors with exact cosine similarity by matrix product."""

    def __init__(
        self,
        *,
        chunk_ids: list[str],
        vectors: np.ndarray,
        model: str,
        corpus_sha256: str,
        built_at: str | None = None,
    ) -> None:
        matrix = normalize_vectors(vectors)
        if matrix.shape[0] != len(chunk_ids):
            raise ValueError("Vector count must match chunk ID count")
        if matrix.shape[1] == 0:
            raise ValueError("Vector dimension must be positive")
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("Chunk IDs in a vector store must be unique")
        if not model:
            raise ValueError("Vector store model must not be empty")
        if not corpus_sha256:
            raise ValueError("Vector store corpus_sha256 must not be empty")
        self.chunk_ids = list(chunk_ids)
        self.vectors = matrix
        self.model = model
        self.dimension = int(matrix.shape[1])
        self.corpus_sha256 = corpus_sha256
        self.built_at = built_at or datetime.now(timezone.utc).isoformat()

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int,
        *,
        exclude_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Compute exact cosine scores and break score ties by ascending chunk ID."""
        if top_k < 0:
            raise ValueError("top_k must not be negative")
        if top_k == 0:
            return []
        query = normalize_vector(query_vector)
        if query.size != self.dimension:
            raise ValueError(
                f"Query dimension {query.size} does not match index "
                f"dimension {self.dimension}"
            )
        scores = self.vectors @ query
        excluded = exclude_ids or set()
        ranked = [
            (chunk_id, float(score))
            for chunk_id, score in zip(self.chunk_ids, scores, strict=True)
            if chunk_id not in excluded
        ]
        ranked.sort(key=lambda result: (-result[1], result[0]))
        return ranked[:top_k]

    def save(self, index_path: str | Path = "data/embeddings/index.npz") -> None:
        """Persist the vector matrix and a versioned JSON manifest."""
        path = Path(index_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as index_file:
            np.savez_compressed(
                index_file,
                chunk_ids=np.asarray(self.chunk_ids, dtype=np.str_),
                vectors=self.vectors,
            )
        manifest = {
            "model": self.model,
            "dimension": self.dimension,
            "corpus_sha256": self.corpus_sha256,
            "built_at": self.built_at,
        }
        manifest_path = path.with_name("manifest.json")
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(
        cls,
        index_path: str | Path = "data/embeddings/index.npz",
        *,
        expected_corpus_sha256: str,
        expected_model: str | None = None,
    ) -> "NumpyVectorStore":
        """Load an index only when its corpus and optional model match."""
        path = Path(index_path)
        manifest_path = path.with_name("manifest.json")
        if not path.is_file() or not manifest_path.is_file():
            raise FileNotFoundError(f"Vector index or manifest not found for {path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("corpus_sha256") != expected_corpus_sha256:
            raise ValueError("Vector index corpus_sha256 does not match current corpus")
        if expected_model is not None and manifest.get("model") != expected_model:
            raise ValueError("Vector index model does not match configured model")
        with np.load(path, allow_pickle=False) as archive:
            chunk_ids = archive["chunk_ids"].astype(str).tolist()
            vectors = np.asarray(archive["vectors"], dtype=np.float32)
        store = cls(
            chunk_ids=chunk_ids,
            vectors=vectors,
            model=manifest["model"],
            corpus_sha256=manifest["corpus_sha256"],
            built_at=manifest["built_at"],
        )
        if manifest.get("dimension") != store.dimension:
            raise ValueError("Vector index dimension does not match its manifest")
        return store
