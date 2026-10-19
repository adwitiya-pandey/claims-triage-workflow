"""BM25 policy retrieval and clause indexing per SPEC 9."""

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "with",
}

VALID_KINDS = {"coverage", "exclusion", "procedure", "documentation", "process"}
DEFAULT_POLICY_PATH = Path("data/policy/clauses.md")


@dataclass(frozen=True)
class Clause:
    """Policy clause representation."""

    chunk_id: str
    kind: str
    procedure_code: str | None
    text: str


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, and drop stopwords."""
    raw_tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in raw_tokens if t not in STOP_WORDS]


class BM25Index:
    """Hand-written BM25 indexer with no external dependencies."""

    def __init__(self, clauses: list[Clause], k1: float = 1.5, b: float = 0.75):
        self.clauses = clauses
        self.k1 = k1
        self.b = b
        self.N = len(clauses)

        self.doc_tokens: list[list[str]] = [tokenize(c.text) for c in clauses]
        self.doc_lens = [len(toks) for toks in self.doc_tokens]
        self.avg_doc_len = (sum(self.doc_lens) / self.N) if self.N > 0 else 1.0

        # Term document frequency
        df: Counter[str] = Counter()
        for toks in self.doc_tokens:
            for term in set(toks):
                df[term] += 1
        self.df = df

        # Compute IDF: ln(1 + (N - n + 0.5) / (n + 0.5))
        self.idf: dict[str, float] = {}
        for term, n in self.df.items():
            self.idf[term] = math.log(1.0 + (self.N - n + 0.5) / (n + 0.5))

    def score_documents(self, query_tokens: list[str]) -> list[tuple[Clause, float]]:
        """Score all clauses against query tokens, breaking ties by chunk_id."""
        scores: list[tuple[Clause, float]] = []
        unique_query = set(query_tokens)

        for i, clause in enumerate(self.clauses):
            dl = self.doc_lens[i]
            doc_term_counts = Counter(self.doc_tokens[i])
            score = 0.0

            for q_term in unique_query:
                if q_term in doc_term_counts:
                    f = doc_term_counts[q_term]
                    idf = self.idf.get(q_term, 0.0)
                    tf_component = (f * (self.k1 + 1.0)) / (
                        f + self.k1 * (1.0 - self.b + self.b * (dl / self.avg_doc_len))
                    )
                    score += idf * tf_component

            scores.append((clause, score))

        # Sort descending by score, then ascending by chunk_id
        scores.sort(key=lambda item: (-item[1], item[0].chunk_id))
        return scores


def parse_clauses_text(text: str, validate_all_procedures: bool = True) -> list[Clause]:
    """Parse clauses markdown content and validate schema."""
    clauses: list[Clause] = []
    seen_ids: set[str] = set()

    blocks = text.strip().split("\n## ")
    for idx, block in enumerate(blocks):
        b = block.strip()
        if not b:
            continue
        if idx == 0 and b.startswith("## "):
            b = b[3:].strip()

        lines = b.splitlines()
        chunk_id = lines[0].strip()

        if chunk_id in seen_ids:
            raise ValueError(f"Duplicate clause ID: {chunk_id}")
        seen_ids.add(chunk_id)

        meta: dict[str, str] = {}
        body_lines: list[str] = []
        in_body = False

        for line in lines[1:]:
            if not in_body:
                if line.strip() == "---":
                    in_body = True
                    continue
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            else:
                body_lines.append(line)

        kind = meta.get("kind", "").strip()
        if kind not in VALID_KINDS:
            raise ValueError(f"Invalid kind '{kind}' for clause {chunk_id}")

        procedure_code = meta.get("procedure_code", "").strip() or None
        if kind == "procedure" and not procedure_code:
            raise ValueError(f"procedure_code required for procedure clause {chunk_id}")

        clause_text = " ".join(body_lines).strip()
        clauses.append(
            Clause(
                chunk_id=chunk_id,
                kind=kind,
                procedure_code=procedure_code,
                text=clause_text,
            )
        )

    if validate_all_procedures:
        proc_codes = {c.procedure_code for c in clauses if c.kind == "procedure"}
        expected_codes = {f"PX{i:04d}" for i in range(1, 13)}
        if proc_codes != expected_codes:
            missing = expected_codes - proc_codes
            raise ValueError(f"Missing required procedure clauses: {missing}")

    return clauses


def load_clauses(path: Path | str | None = None) -> list[Clause]:
    """Load and validate policy clauses from markdown file."""
    p = Path(path) if path else DEFAULT_POLICY_PATH
    with open(p, "r", encoding="utf-8") as f:
        content = f.read()
    return parse_clauses_text(content, validate_all_procedures=True)


def compute_corpus_sha256(path: Path | str | None = None, content: str | None = None) -> str:
    """Compute SHA-256 hash of the policy corpus."""
    if content is None:
        p = Path(path) if path else DEFAULT_POLICY_PATH
        with open(p, "rb") as f:
            content_bytes = f.read()
    else:
        content_bytes = content.encode("utf-8")

    return hashlib.sha256(content_bytes).hexdigest()


def retrieve(
    query: str,
    procedure_code: str | None,
    top_k: int = 4,
    clauses: list[Clause] | None = None,
) -> list[dict]:
    """Retrieve the procedure clause plus top_k other clauses ranked by BM25.

    The procedure clause is always returned first.
    Other clauses are ranked by BM25 with tie-break on chunk_id.
    """
    all_clauses = clauses if clauses is not None else load_clauses()

    # Find the procedure clause if procedure_code is provided
    procedure_clause: Clause | None = None
    if procedure_code:
        for c in all_clauses:
            if c.kind == "procedure" and c.procedure_code == procedure_code:
                procedure_clause = c
                break

    # Clauses to rank with BM25 (excluding the chosen procedure clause)
    other_clauses = [c for c in all_clauses if c is not procedure_clause]

    index = BM25Index(other_clauses)
    query_tokens = tokenize(query)
    ranked = index.score_documents(query_tokens)

    results: list[dict] = []
    if procedure_clause is not None:
        # Score procedure clause for completeness
        proc_idx = BM25Index([procedure_clause])
        proc_score = proc_idx.score_documents(query_tokens)[0][1]
        results.append(
            {
                "chunk_id": procedure_clause.chunk_id,
                "kind": procedure_clause.kind,
                "text": procedure_clause.text,
                "score": round(proc_score, 4),
            }
        )

    for clause, score in ranked[:top_k]:
        results.append(
            {
                "chunk_id": clause.chunk_id,
                "kind": clause.kind,
                "text": clause.text,
                "score": round(score, 4),
            }
        )

    return results
