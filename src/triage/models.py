"""Pydantic models and data contracts per SPEC 7."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
)

# Regex patterns (ASCII digits only, re.ASCII)
CLAIM_ID_REGEX = r"^CLM[0-9]{8}$"
MEMBER_ID_REGEX = r"^M[0-9]{6}$"
PLAN_ID_REGEX = r"^PLN[0-9]{3}$"
PROVIDER_ID_REGEX = r"^PRV[0-9]{4}$"
PROCEDURE_CODE_REGEX = r"^PX[0-9]{4}$"
CLAUSE_ID_REGEX = r"^POL-[A-Z0-9-]{3,24}$"
REVIEWER_ID_REGEX = r"^[a-z][a-z0-9._-]{2,31}$"

ClaimId = Annotated[str, StringConstraints(pattern=CLAIM_ID_REGEX, strict=True)]
MemberId = Annotated[str, StringConstraints(pattern=MEMBER_ID_REGEX, strict=True)]
PlanId = Annotated[str, StringConstraints(pattern=PLAN_ID_REGEX, strict=True)]
ProviderId = Annotated[str, StringConstraints(pattern=PROVIDER_ID_REGEX, strict=True)]
ProcedureCode = Annotated[str, StringConstraints(pattern=PROCEDURE_CODE_REGEX, strict=True)]
ClauseId = Annotated[str, StringConstraints(pattern=CLAUSE_ID_REGEX, strict=True)]
ReviewerId = Annotated[str, StringConstraints(pattern=REVIEWER_ID_REGEX, strict=True)]

_claim_adapter = TypeAdapter(ClaimId)
_member_adapter = TypeAdapter(MemberId)
_plan_adapter = TypeAdapter(PlanId)
_provider_adapter = TypeAdapter(ProviderId)
_procedure_adapter = TypeAdapter(ProcedureCode)
_clause_adapter = TypeAdapter(ClauseId)
_reviewer_adapter = TypeAdapter(ReviewerId)


def validate_claim_id(v: str) -> str:
    """Validate claim ID format CLM[0-9]{8}."""
    return _claim_adapter.validate_python(v)


def validate_member_id(v: str) -> str:
    """Validate member ID format M[0-9]{6}."""
    return _member_adapter.validate_python(v)


def validate_plan_id(v: str) -> str:
    """Validate plan ID format PLN[0-9]{3}."""
    return _plan_adapter.validate_python(v)


def validate_provider_id(v: str) -> str:
    """Validate provider ID format PRV[0-9]{4}."""
    return _provider_adapter.validate_python(v)


def validate_procedure_code(v: str) -> str:
    """Validate procedure code format PX[0-9]{4}."""
    return _procedure_adapter.validate_python(v)


def validate_clause_id(v: str) -> str:
    """Validate clause ID format POL-[A-Z0-9-]{3,24}."""
    return _clause_adapter.validate_python(v)


def validate_reviewer_id(v: str) -> str:
    """Validate reviewer ID format [a-z][a-z0-9._-]{2,31}."""
    return _reviewer_adapter.validate_python(v)


DocumentPresent = Literal[
    "claim_form",
    "itemized_bill",
    "referral",
    "medical_report",
    "preauth_letter",
    "id_proof",
]

Route = Literal["FAST_TRACK", "STANDARD_REVIEW", "ESCALATE"]
ClauseEffect = Literal["supports", "restricts", "neutral"]


class Extraction(BaseModel):
    """Extraction model from LLM extraction step."""

    model_config = ConfigDict(extra="forbid")

    claim_id: ClaimId
    member_id: MemberId
    provider_id: ProviderId
    procedure_code: ProcedureCode | None = None
    procedure_description: str = Field(..., max_length=200)
    billed_amount: float = Field(..., ge=0.0)
    service_date: str = Field(..., pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    preauth_reference: str | None = Field(default=None, max_length=40)
    documents_present: list[DocumentPresent] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    unreadable_fields: list[str] = Field(default_factory=list)


class ClauseItem(BaseModel):
    """Citation or clause assessment item."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: ClauseId
    effect: ClauseEffect
    quote: str = Field(..., max_length=300)


class Assessment(BaseModel):
    """Assessment model from LLM policy assessment step."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(..., max_length=700)
    clauses: list[ClauseItem] = Field(default_factory=list)
    missing_items: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(..., ge=0.0, le=1.0)


class RuleHit(BaseModel):
    """Deterministic rule hit."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    route: Route
    evidence: list[str] = Field(default_factory=list)


class ReviewAction(BaseModel):
    """Action submitted by a human reviewer.

    Note: DENY, APPROVE, and PAY do not exist.
    """

    model_config = ConfigDict(extra="forbid")

    reviewer_id: ReviewerId
    action: Literal["CONFIRM", "ESCALATE_FURTHER"]
    reason: str = Field(..., min_length=10, max_length=500)


class TrailEntry(BaseModel):
    """Single node execution log entry in the audit trail."""

    model_config = ConfigDict(extra="forbid")

    node: str
    started_at: str
    ended_at: str
    outcome: str
    detail_codes: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    """Auditable final decision record stored in decisions table."""

    model_config = ConfigDict(extra="forbid")

    claim_id: ClaimId
    version: int = Field(..., ge=1)
    route_recommended: Route
    route_final: Route
    reasons: list[RuleHit]
    citations: list[ClauseItem] = Field(default_factory=list)
    summary: str = ""
    missing_items: list[str] = Field(default_factory=list)
    extraction: dict | None = None
    facts_digest: dict = Field(default_factory=dict)
    review: dict | None = None
    versions: dict[str, str] = Field(default_factory=dict)
    llm_usage: dict[str, int] = Field(default_factory=dict)
    trail: list[dict] = Field(default_factory=list)
    created_at: str
