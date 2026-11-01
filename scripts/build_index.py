"""Build the corpus-matched dense policy index."""

import argparse
from pathlib import Path

from triage.config import TriageConfig
from triage.embeddings import (
    CachedEmbedder,
    Embedder,
    GeminiEmbedder,
)
from triage.retrieval import compute_corpus_sha256, load_corpus
from triage.vectorstore import NumpyVectorStore


def build_index(
    *,
    corpus_path: str | Path = "data/policy/clauses.md",
    index_path: str | Path = "data/embeddings/index.npz",
    cache_path: str | Path = "data/embeddings/cache.jsonl",
    embedder: Embedder | None = None,
    allow_live: bool = False,
) -> NumpyVectorStore:
    """Embed non-procedure policy clauses and persist their exact-search index."""
    corpus = load_corpus(corpus_path)
    clauses = [clause for clause in corpus if clause.kind != "procedure"]
    config = TriageConfig.load()
    if embedder is None:
        base_embedder = GeminiEmbedder(
            model=config.EMBEDDING_MODEL,
            api_key=config.GEMINI_API_KEY,
        )
        embedder = CachedEmbedder(
            base_embedder,
            cache_path=cache_path,
            dimension=768,
            replay=not allow_live,
        )
    vectors = embedder.embed_documents([clause.text for clause in clauses])
    model = getattr(embedder, "model", config.EMBEDDING_MODEL)
    store = NumpyVectorStore(
        chunk_ids=[clause.chunk_id for clause in clauses],
        vectors=vectors,
        model=model,
        corpus_sha256=compute_corpus_sha256(corpus),
    )
    store.save(index_path)
    return store


def main() -> None:
    """Build the index; uncached Gemini calls require the explicit live flag."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=Path("data/policy/clauses.md"))
    parser.add_argument("--index", type=Path, default=Path("data/embeddings/index.npz"))
    parser.add_argument(
        "--cache", type=Path, default=Path("data/embeddings/cache.jsonl")
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Permit missing document vectors to be requested from Gemini",
    )
    args = parser.parse_args()
    store = build_index(
        corpus_path=args.corpus,
        index_path=args.index,
        cache_path=args.cache,
        allow_live=args.allow_live,
    )
    print(
        f"Built {len(store.chunk_ids)} vectors "
        f"({store.dimension} dimensions, corpus {store.corpus_sha256})."
    )


if __name__ == "__main__":
    main()
