"""Policy corpus loader, validator, BM25 indexing, and retrieval engine."""

import hashlib
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VALID_KINDS = {"coverage", "exclusion", "procedure", "documentation", "process"}
STOP_WORDS = {
    "a",
    "about",
    "above",
    "after",
    "again",
    "against",
    "all",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "could",
    "did",
    "do",
    "does",
    "doing",
    "down",
    "during",
    "each",
    "few",
    "for",
    "from",
    "further",
    "had",
    "has",
    "have",
    "having",
    "he",
    "her",
    "here",
    "hers",
    "herself",
    "him",
    "himself",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "itself",
    "me",
    "more",
    "most",
    "my",
    "myself",
    "no",
    "nor",
    "not",
    "of",
    "off",
    "on",
    "once",
    "only",
    "or",
    "other",
    "ought",
    "our",
    "ours",
    "ourselves",
    "out",
    "over",
    "own",
    "same",
    "she",
    "should",
    "so",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "themselves",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "until",
    "up",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "with",
    "would",
    "you",
    "your",
    "yours",
    "yourself",
    "yourselves",
}


@dataclass(frozen=True)
class PolicyClause:
    """A policy clause from the markdown corpus."""

    chunk_id: str
    kind: str
    procedure_code: str | None
    text: str


def tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase alphanumeric terms excluding stop words."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if t not in STOP_WORDS]


def parse_clauses_markdown(content: str) -> list[PolicyClause]:
    """Parse clauses markdown content into PolicyClause objects and validate."""
    blocks = re.split(r"(?m)^##\s+", content)
    clauses: list[PolicyClause] = []
    seen_ids: set[str] = set()

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.splitlines()
        header = lines[0].strip()
        chunk_id = header

        metadata_lines = []
        body_lines = []
        in_body = False

        for line in lines[1:]:
            if line.strip() == "---":
                in_body = True
                continue
            if in_body:
                body_lines.append(line)
            else:
                metadata_lines.append(line)

        kind: str | None = None
        procedure_code: str | None = None

        for m_line in metadata_lines:
            m_line = m_line.split("#")[0].strip()  # strip inline comments
            if not m_line:
                continue
            if ":" in m_line:
                k, v = m_line.split(":", 1)
                k = k.strip()
                v = v.strip()
                if k == "kind":
                    kind = v
                elif k == "procedure_code":
                    procedure_code = v if v else None

        text = "\n".join(body_lines).strip()
        if not chunk_id:
            raise ValueError("Malformed clause: missing chunk_id header")
        if chunk_id in seen_ids:
            raise ValueError(f"Duplicate clause ID found: {chunk_id}")
        seen_ids.add(chunk_id)

        if not kind or kind not in VALID_KINDS:
            raise ValueError(f"Clause {chunk_id} has invalid or missing kind: {kind}")

        if kind == "procedure":
            if not procedure_code:
                raise ValueError(
                    f"Procedure clause {chunk_id} must have procedure_code set"
                )
        else:
            if procedure_code is not None:
                raise ValueError(
                    f"Non-procedure clause {chunk_id} should not have procedure_code"
                )

        if not text:
            raise ValueError(f"Clause {chunk_id} has empty body text")

        clauses.append(
            PolicyClause(
                chunk_id=chunk_id,
                kind=kind,
                procedure_code=procedure_code,
                text=text,
            )
        )

    # Validate that every procedure code PX0001..PX0012 has exactly one clause
    proc_codes = {f"PX{i:04d}" for i in range(1, 13)}
    found_proc_codes = {c.procedure_code for c in clauses if c.kind == "procedure"}
    missing = proc_codes - found_proc_codes
    if missing:
        raise ValueError(f"Missing procedure clauses for codes: {sorted(missing)}")

    return clauses


def load_corpus(path: str | Path = "data/policy/clauses.md") -> list[PolicyClause]:
    """Load policy corpus from disk."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Policy corpus file not found: {path}")
    content = p.read_text(encoding="utf-8")
    return parse_clauses_markdown(content)


def compute_corpus_sha256(clauses: list[PolicyClause]) -> str:
    """Compute deterministic SHA256 hash over canonical representation of clauses."""
    hasher = hashlib.sha256()
    sorted_clauses = sorted(clauses, key=lambda c: c.chunk_id)
    for c in sorted_clauses:
        chunk_rep = f"{c.chunk_id}|{c.kind}|{c.procedure_code or ''}|{c.text}\n"
        hasher.update(chunk_rep.encode("utf-8"))
    return hasher.hexdigest()


class BM25Index:
    """Hand-written BM25 ranking implementation per SPEC 9."""

    def __init__(
        self, clauses: list[PolicyClause], k1: float = 1.5, b: float = 0.75
    ) -> None:
        self.k1 = k1
        self.b = b
        self.clauses = clauses
        self.N = len(clauses)

        self.doc_tokens: list[list[str]] = [tokenize(c.text) for c in clauses]
        self.doc_lens: list[int] = [len(tokens) for tokens in self.doc_tokens]
        self.avg_doc_len: float = sum(self.doc_lens) / self.N if self.N > 0 else 1.0

        # Document frequencies for terms
        self.df: dict[str, int] = {}
        for tokens in self.doc_tokens:
            for term in set(tokens):
                self.df[term] = self.df.get(term, 0) + 1

    def idf(self, term: str) -> float:
        """SPEC 9: idf = ln(1 + (N - n + 0.5) / (n + 0.5))."""
        n = self.df.get(term, 0)
        return math.log(1.0 + (self.N - n + 0.5) / (n + 0.5))

    def score(self, query_tokens: list[str], doc_idx: int) -> float:
        """Calculate BM25 score for a document given query tokens."""
        doc_tokens = self.doc_tokens[doc_idx]
        doc_len = self.doc_lens[doc_idx]
        if doc_len == 0:
            return 0.0

        # Term frequencies in document
        tf_dict: dict[str, int] = {}
        for t in doc_tokens:
            tf_dict[t] = tf_dict.get(t, 0) + 1

        total_score = 0.0
        for term in query_tokens:
            if term not in tf_dict:
                continue
            tf = tf_dict[term]
            term_idf = self.idf(term)
            numerator = tf * (self.k1 + 1.0)
            denominator = tf + self.k1 * (
                1.0 - self.b + self.b * (doc_len / self.avg_doc_len)
            )
            total_score += term_idf * (numerator / denominator)

        return total_score


# Global cached corpus and index for fast reuse
_CACHED_CORPUS: list[PolicyClause] | None = None
_CACHED_INDEX: BM25Index | None = None

RETRIEVER_MODES = {"bm25", "dense", "hybrid"}


def get_default_index() -> BM25Index:
    """Get or load default BM25 index."""
    global _CACHED_CORPUS, _CACHED_INDEX
    if _CACHED_INDEX is None:
        _CACHED_CORPUS = load_corpus()
        _CACHED_INDEX = BM25Index(_CACHED_CORPUS)
    return _CACHED_INDEX


def reciprocal_rank_fusion(
    rankings: dict[str, list[str]], rank_constant: int = 60
) -> dict[str, float]:
    """Fuse ranked chunk IDs with the standard reciprocal-rank formula."""
    if rank_constant < 1:
        raise ValueError("rank_constant must be positive")
    fused_scores: dict[str, float] = {}
    for ranking in rankings.values():
        for rank, chunk_id in enumerate(ranking, 1):
            fused_scores[chunk_id] = fused_scores.get(chunk_id, 0.0) + (
                1.0 / (rank_constant + rank)
            )
    return fused_scores


def retrieve_with_trace(
    query: str,
    procedure_code: str,
    top_k: int = 4,
    clauses: list[PolicyClause] | None = None,
    *,
    mode: str | None = None,
    embedder: Any | None = None,
    vector_store: Any | None = None,
    replay: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, list[str]]:
    """Return ranked policy chunks, ranking evidence, actual mode, and guardrails."""
    selected_mode = (mode or os.getenv("RETRIEVER", "bm25")).lower()
    if selected_mode not in RETRIEVER_MODES:
        raise ValueError(f"Unsupported RETRIEVER mode: {selected_mode}")
    if top_k < 0:
        raise ValueError("top_k must not be negative")

    if clauses is None:
        index = get_default_index()
        all_clauses = index.clauses
    else:
        index = BM25Index(clauses)
        all_clauses = clauses

    query_tokens = tokenize(f"{query} {procedure_code}")
    proc_clause_idx = next(
        (
            idx
            for idx, clause in enumerate(all_clauses)
            if clause.kind == "procedure" and clause.procedure_code == procedure_code
        ),
        -1,
    )
    candidates = [
        (idx, clause)
        for idx, clause in enumerate(all_clauses)
        if idx != proc_clause_idx
    ]
    candidate_ids = {clause.chunk_id for _, clause in candidates}
    dense_candidate_ids = {
        clause.chunk_id for _, clause in candidates if clause.kind != "procedure"
    }

    bm25_scores = {
        clause.chunk_id: index.score(query_tokens, idx) for idx, clause in candidates
    }
    bm25_ranked = sorted(
        candidate_ids,
        key=lambda chunk_id: (-bm25_scores[chunk_id], chunk_id),
    )
    bm25_ranks = {chunk_id: rank for rank, chunk_id in enumerate(bm25_ranked, 1)}

    actual_mode = selected_mode
    guardrail_flags: list[str] = []
    dense_scores: dict[str, float] = {}
    dense_ranks: dict[str, int] = {}
    fused_scores: dict[str, float] = {}
    dense_ranked: list[str] = []

    if selected_mode != "bm25":
        try:
            if embedder is None or vector_store is None:
                raise ValueError(
                    "Dense retrieval requires an embedder and vector index"
                )
            from triage.sanitize import sanitize_text

            clean_query, _ = sanitize_text(query)
            query_vector = embedder.embed_query(clean_query)
            dense_results = vector_store.search(
                query_vector,
                top_k=len(dense_candidate_ids),
                exclude_ids={
                    clause.chunk_id
                    for clause in all_clauses
                    if clause.kind == "procedure"
                },
            )
            dense_ranked = [
                chunk_id
                for chunk_id, _ in dense_results
                if chunk_id in dense_candidate_ids
            ]
            if set(dense_ranked) != dense_candidate_ids:
                raise ValueError("Dense index does not match the non-procedure corpus")
            dense_scores = {chunk_id: float(score) for chunk_id, score in dense_results}
            dense_ranks = {
                chunk_id: rank for rank, chunk_id in enumerate(dense_ranked, 1)
            }
            fused_scores = reciprocal_rank_fusion(
                {"bm25": bm25_ranked, "dense": dense_ranked}
            )
        except Exception:
            if replay:
                raise
            # A failed dense branch is visible to the caller and falls back safely.
            actual_mode = "bm25"
            guardrail_flags.append("dense_unavailable")

    if actual_mode == "dense":
        ranked_ids = dense_ranked
    elif actual_mode == "hybrid":
        ranked_ids = sorted(
            candidate_ids,
            key=lambda chunk_id: (-fused_scores.get(chunk_id, 0.0), chunk_id),
        )
    else:
        ranked_ids = bm25_ranked
    selected_ids = ranked_ids[:top_k]
    clauses_by_id = {clause.chunk_id: clause for clause in all_clauses}

    trace: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    procedure_clause = all_clauses[proc_clause_idx] if proc_clause_idx >= 0 else None
    selected_with_procedure = (
        [procedure_clause] if procedure_clause is not None else []
    ) + [clauses_by_id[chunk_id] for chunk_id in selected_ids]

    for clause in selected_with_procedure:
        is_procedure = clause.kind == "procedure"
        bm25_score = (
            index.score(query_tokens, all_clauses.index(clause))
            if is_procedure
            else bm25_scores[clause.chunk_id]
        )
        trace_entry = {
            "chunk_id": clause.chunk_id,
            "bm25_rank": bm25_ranks.get(clause.chunk_id),
            "bm25_score": round(bm25_score, 4),
            "dense_rank": dense_ranks.get(clause.chunk_id),
            "dense_score": (
                round(dense_scores[clause.chunk_id], 8)
                if clause.chunk_id in dense_scores
                else None
            ),
            "fused_score": fused_scores.get(clause.chunk_id),
        }
        if actual_mode == "dense":
            score = dense_scores[clause.chunk_id] if not is_procedure else bm25_score
        elif actual_mode == "hybrid":
            score = fused_scores[clause.chunk_id] if not is_procedure else bm25_score
        else:
            score = bm25_score
        result = {
            "chunk_id": clause.chunk_id,
            "kind": clause.kind,
            "text": clause.text,
            "score": round(score, 4),
            **trace_entry,
        }
        trace.append(trace_entry)
        results.append(result)

    return results, trace, actual_mode, guardrail_flags


def retrieve(
    query: str,
    procedure_code: str,
    top_k: int = 4,
    clauses: list[PolicyClause] | None = None,
    *,
    mode: str | None = None,
    embedder: Any | None = None,
    vector_store: Any | None = None,
    replay: bool = False,
) -> list[dict[str, Any]]:
    """Retrieve policy chunks relevant to the claim.

    SPEC 9:
    Query = extracted procedure_description + procedure_code.
    Returns the procedure clause first plus top_k other clauses. The retriever
    defaults to BM25; dense and hybrid modes need an embedder and vector index.
    """
    results, _, _, _ = retrieve_with_trace(
        query=query,
        procedure_code=procedure_code,
        top_k=top_k,
        clauses=clauses,
        mode=mode,
        embedder=embedder,
        vector_store=vector_store,
        replay=replay,
    )
    return results
