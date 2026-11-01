"""Offline-replayable evaluation for policy retrieval relevance."""

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from triage.retrieval import load_corpus, retrieve_with_trace

DEFAULT_GOLD_PATH = Path("evals/retrieval_gold.jsonl")
DEFAULT_REPORT_PATH = Path("evals/reports/retrieval.md")
DEFAULT_INDEX_PATH = Path("data/embeddings/index.npz")
DEFAULT_CACHE_PATH = Path("data/embeddings/cache.jsonl")


@dataclass(frozen=True)
class RetrievalEvaluation:
    """Aggregate metric values and reproducibility metadata for one mode."""

    metrics: dict[str, float]
    case_count: int
    model: str


def load_gold_set(path: str | Path = DEFAULT_GOLD_PATH) -> list[dict[str, Any]]:
    """Load retrieval labels, requiring the query text to be a procedure code only."""
    records = []
    seen_case_ids: set[str] = set()
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), 1
    ):
        record = json.loads(line)
        required = {
            "case_id",
            "query_text",
            "procedure_code",
            "relevant_chunk_ids",
        }
        if not required.issubset(record):
            raise ValueError(f"Retrieval label at line {line_number} is incomplete")
        if record["case_id"] in seen_case_ids:
            raise ValueError(f"Duplicate retrieval case ID at line {line_number}")
        if record["query_text"] != record["procedure_code"]:
            raise ValueError(
                f"Retrieval query at line {line_number} must not contain claim text"
            )
        if not record["relevant_chunk_ids"]:
            raise ValueError(f"Retrieval label at line {line_number} has no relevance")
        seen_case_ids.add(record["case_id"])
        records.append(record)
    if not records:
        raise ValueError(f"No retrieval labels found in {path}")
    return records


def calculate_metrics(
    relevant_chunk_ids: set[str], ranked_chunk_ids: list[str]
) -> dict[str, float]:
    """Calculate binary Recall@k, reciprocal rank, and nDCG@4 for one query."""
    relevant = set(relevant_chunk_ids)
    if not relevant:
        return {
            "recall@1": 0.0,
            "recall@3": 0.0,
            "recall@4": 0.0,
            "mrr": 0.0,
            "ndcg@4": 0.0,
        }

    recalls = {
        f"recall@{k}": len(relevant.intersection(ranked_chunk_ids[:k])) / len(relevant)
        for k in (1, 3, 4)
    }
    reciprocal_rank = next(
        (
            1.0 / rank
            for rank, chunk_id in enumerate(ranked_chunk_ids, 1)
            if chunk_id in relevant
        ),
        0.0,
    )
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, chunk_id in enumerate(ranked_chunk_ids[:4], 1)
        if chunk_id in relevant
    )
    ideal_relevant = min(len(relevant), 4)
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_relevant + 1))

    return {
        **recalls,
        "mrr": reciprocal_rank,
        "ndcg@4": round(dcg / ideal_dcg, 16),
    }


def _build_runtime_query(
    record: dict[str, Any], cases_dir: str | Path = "data/cases"
) -> str:
    """Reconstruct the synthetic retrieval query in memory, without persisting it."""
    case_number = record["case_id"].removeprefix("CASE-")
    case_path = Path(cases_dir) / f"case-{case_number.lower()}.json"
    case_data = json.loads(case_path.read_text(encoding="utf-8"))
    description = (case_data.get("gold_extraction") or {}).get("procedure_description")
    if not isinstance(description, str) or not description:
        raise ValueError(f"Missing procedure description for {record['case_id']}")
    return f"{description} {record['procedure_code']}".strip()


def _dense_components(
    corpus: list[Any],
    index_path: str | Path = DEFAULT_INDEX_PATH,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    *,
    allow_live: bool = False,
) -> tuple[Any, Any, str]:
    """Load the Gemini embedder cache and a corpus-matched persisted vector index."""
    from triage.config import TriageConfig
    from triage.embeddings import CachedEmbedder, GeminiEmbedder
    from triage.retrieval import compute_corpus_sha256
    from triage.vectorstore import NumpyVectorStore

    config = TriageConfig.load()
    embedder = CachedEmbedder(
        GeminiEmbedder(model=config.EMBEDDING_MODEL, api_key=config.GEMINI_API_KEY),
        cache_path=cache_path,
        dimension=768,
        replay=not allow_live,
    )
    vector_store = NumpyVectorStore.load(
        index_path,
        expected_corpus_sha256=compute_corpus_sha256(corpus),
        expected_model=config.EMBEDDING_MODEL,
    )
    return embedder, vector_store, config.EMBEDDING_MODEL


def evaluate_retrieval(
    mode: str = "bm25",
    *,
    gold_path: str | Path = DEFAULT_GOLD_PATH,
    corpus_path: str | Path = "data/policy/clauses.md",
    cases_dir: str | Path = "data/cases",
    embedder: Any | None = None,
    vector_store: Any | None = None,
    allow_live: bool = False,
) -> RetrievalEvaluation:
    """Run one retriever over the labelled cases and macro-average its metrics."""
    if mode not in {"bm25", "dense", "hybrid"}:
        raise ValueError(f"Unsupported retrieval mode: {mode}")

    corpus = load_corpus(corpus_path)
    model_name = "none"
    if mode != "bm25" and (embedder is None or vector_store is None):
        embedder, vector_store, model_name = _dense_components(
            corpus, allow_live=allow_live
        )
    elif embedder is not None:
        model_name = getattr(embedder, "model", "injected")

    per_query = []
    for record in load_gold_set(gold_path):
        chunks, _, _, flags = retrieve_with_trace(
            query=_build_runtime_query(record, cases_dir),
            procedure_code=record["procedure_code"],
            top_k=4,
            clauses=corpus,
            mode=mode,
            embedder=embedder,
            vector_store=vector_store,
            replay=not allow_live,
        )
        if flags:
            raise RuntimeError(
                f"{mode} retrieval fell back to BM25 for case {record['case_id']}"
            )
        per_query.append(
            calculate_metrics(
                set(record["relevant_chunk_ids"]),
                [chunk["chunk_id"] for chunk in chunks],
            )
        )

    return RetrievalEvaluation(
        metrics={
            key: round(sum(row[key] for row in per_query) / len(per_query), 4)
            for key in ("recall@1", "recall@3", "recall@4", "mrr", "ndcg@4")
        },
        case_count=len(per_query),
        model=model_name,
    )


def write_report(
    results: dict[str, RetrievalEvaluation],
    path: str | Path = DEFAULT_REPORT_PATH,
) -> None:
    """Write measured retrieval metrics without query text or claim content."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    models = sorted({result.model for result in results.values()})
    lines = [
        "# Retrieval Experiment",
        "",
        f"- Date: {date.today().isoformat()}",
        f"- Embedding model(s): {', '.join(models)}",
        "- Dataset: synthetic labels store procedure-code query handles only; "
        "gold descriptions are combined in memory and never persisted.",
        "",
        "| Mode | Recall@1 | Recall@3 | Recall@4 | MRR | nDCG@4 | Cases |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for mode, result in results.items():
        metrics = result.metrics
        lines.append(
            f"| {mode} | {metrics['recall@1']:.4f} | {metrics['recall@3']:.4f} | "
            f"{metrics['recall@4']:.4f} | {metrics['mrr']:.4f} | "
            f"{metrics['ndcg@4']:.4f} | {result.case_count} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    """Run retrieval evaluation for one mode or all modes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=["bm25", "dense", "hybrid", "all"],
        default="bm25",
    )
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Permit missing embeddings to be requested from Gemini",
    )
    args = parser.parse_args()

    modes = ("bm25", "dense", "hybrid") if args.mode == "all" else (args.mode,)
    results = {
        mode: evaluate_retrieval(
            mode,
            gold_path=args.gold,
            allow_live=args.allow_live,
        )
        for mode in modes
    }
    for mode, result in results.items():
        metrics = result.metrics
        print(
            f"{mode:<7} "
            f"Recall@1={metrics['recall@1']:.4f} "
            f"Recall@3={metrics['recall@3']:.4f} "
            f"Recall@4={metrics['recall@4']:.4f} "
            f"MRR={metrics['mrr']:.4f} "
            f"nDCG@4={metrics['ndcg@4']:.4f} "
            f"cases={result.case_count}"
        )
    if args.mode == "all":
        write_report(results, args.output)


if __name__ == "__main__":
    main()
