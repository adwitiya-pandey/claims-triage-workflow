"""Graph state TypedDict definitions per SPEC 6."""

import operator
from typing import Annotated, TypedDict


class GraphState(TypedDict, total=False):
    """LangGraph state holding only plain JSON-serializable values.

    All Pydantic models are serialized with .model_dump(mode="json")
    before entering state and re-validated upon exit.
    """

    claim_id: str
    version: int
    raw_text: str
    clean_text: str
    injection_flags: list[str]
    extraction: dict | None
    extraction_failed: bool
    facts: dict
    tool_error: str | None
    claim_missing: bool
    chunks: list[dict]
    assessment: dict | None
    route: str | None
    rule_hits: list[dict]
    review: dict | None
    decision: dict | None
    trail: Annotated[list[dict], operator.add]
