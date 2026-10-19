"""Deterministic rule engine for triage decisions per SPEC 11.

Rule Table:
| ID  | Condition                                                                  | Route           |
|-----|----------------------------------------------------------------------------|-----------------|
| R01 | Claim not found in the system of record                                    | ESCALATE        |
| R02 | No coverage rule for the plan and procedure                                | ESCALATE        |
| R03 | Rule says covered = false (the AI cannot deny; a human decides)            | STANDARD_REVIEW |
| R04 | Rule requires preauthorisation and the submission has no preauth_reference | STANDARD_REVIEW |
| R05 | max_allowed is set and billed_amount exceeds it                            | STANDARD_REVIEW |
| R06 | billed_amount >= HIGH_AMOUNT_THRESHOLD                                     | ESCALATE        |
| R07 | Provider is OUT_OF_NETWORK                                                 | STANDARD_REVIEW |
| R08 | Duplicate claim exists: status APPROVED or PAID -> ESCALATE; else STD_REV  | as stated       |
| R09 | remaining_annual_limit < billed_amount                                     | STANDARD_REVIEW |
| R10 | Extracted fields differ from the claim record                              | ESCALATE        |
| R11 | Days from service_date to submitted_on > TIMELY_FILING_DAYS (90 allowed)   | STANDARD_REVIEW |
| R12 | Injection flag from the submission text or any note                        | ESCALATE        |
| R13 | Extraction failed or confidence < MIN_EXTRACT_CONFIDENCE                   | ESCALATE        |
| R14 | A required document for this procedure is not in documents_present         | STANDARD_REVIEW |
| R15 | No valid supports clause after the citation checks                         | ESCALATE        |
| R16 | Claim status is not SUBMITTED or PENDED                                    | ESCALATE        |
| R17 | At least one valid restricts clause                                        | STANDARD_REVIEW |
| R18 | Facts unavailable because a tool failed after retries                      | ESCALATE        |
"""

from datetime import date
from typing import Any

from triage.models import Route, RuleHit

RANK: dict[Route, int] = {
    "FAST_TRACK": 0,
    "STANDARD_REVIEW": 1,
    "ESCALATE": 2,
}

REV_RANK: dict[int, Route] = {v: k for k, v in RANK.items()}


def _resolve_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "high_amount": cfg.get("high_amount", 10000.0) if cfg else 10000.0,
        "filing_days": cfg.get("filing_days", 90) if cfg else 90,
        "min_conf": cfg.get("min_conf", 0.7) if cfg else 0.7,
    }


def evaluate_rules(
    facts: dict[str, Any], cfg: dict[str, Any] | None = None
) -> tuple[Route, list[RuleHit]]:
    """Pure rule evaluation function returning recommended route and RuleHit objects."""
    config = _resolve_config(cfg)
    raw_hits: list[tuple[str, Route, list[str]]] = []

    def hit(rule_id: str, route_type: Route, evidence: list[str]) -> None:
        raw_hits.append((rule_id, route_type, evidence))

    # Pre-checks and early exits
    if facts.get("extraction_failed") or facts.get("confidence", 1.0) < config["min_conf"]:
        ev = ["extraction_failed"] if facts.get("extraction_failed") else ["extraction.confidence"]
        hit("R13", "ESCALATE", ev)

    if facts.get("injection_flags"):
        hit("R12", "ESCALATE", ["injection_flags"])

    if facts.get("claim_missing"):
        hit("R01", "ESCALATE", ["get_claim"])

    if facts.get("tool_error"):
        hit("R18", "ESCALATE", ["tool_error"])

    # If critical data is missing, terminate early (R12 is reported alongside)
    if facts.get("claim_missing") or facts.get("extraction_failed") or facts.get("tool_error"):
        # Deduplicate hits by rule_id
        seen = set()
        deduped: list[RuleHit] = []
        for r_id, rt, ev in raw_hits:
            if r_id not in seen:
                seen.add(r_id)
                deduped.append(RuleHit(rule_id=r_id, route=rt, evidence=ev))
        deduped.sort(key=lambda h: h.rule_id)
        return "ESCALATE", deduped

    # System of record checks
    c = facts["claim"]
    ex = facts.get("extracted", {})

    if c.get("status") not in ("SUBMITTED", "PENDED"):
        hit("R16", "ESCALATE", ["get_claim.status"])

    for k in ("member_id", "procedure_code", "provider_id", "service_date"):
        if ex.get(k) != c.get(k):
            hit("R10", "ESCALATE", [f"get_claim.{k}"])

    if (
        "billed_amount" in ex
        and "billed_amount" in c
        and abs(float(ex["billed_amount"]) - float(c["billed_amount"])) > 0.01
    ):
        hit("R10", "ESCALATE", ["get_claim.billed_amount"])

    rule = facts.get("coverage_rule")
    if rule is None:
        hit("R02", "ESCALATE", ["get_coverage_rule"])
    else:
        if not rule.get("covered"):
            hit("R03", "STANDARD_REVIEW", ["get_coverage_rule.covered"])
        if rule.get("requires_preauth") and not ex.get("preauth_reference"):
            hit("R04", "STANDARD_REVIEW", ["get_coverage_rule.requires_preauth"])
        if rule.get("max_allowed") is not None and c["billed_amount"] > rule["max_allowed"]:
            hit(
                "R05",
                "STANDARD_REVIEW",
                ["get_coverage_rule.max_allowed", "get_claim.billed_amount"],
            )

    if c.get("billed_amount", 0.0) >= config["high_amount"]:
        hit("R06", "ESCALATE", ["get_claim.billed_amount"])

    if c.get("provider_network_status") == "OUT_OF_NETWORK":
        hit("R07", "STANDARD_REVIEW", ["get_claim.provider_network_status"])

    for d in facts.get("duplicates", []):
        d_route: Route = (
            "ESCALATE" if d.get("status") in ("APPROVED", "PAID") else "STANDARD_REVIEW"
        )
        hit("R08", d_route, ["list_member_claims.duplicates"])

    if facts.get("remaining_annual_limit") is not None and facts["remaining_annual_limit"] < c.get(
        "billed_amount", 0.0
    ):
        hit("R09", "STANDARD_REVIEW", ["get_member_ytd_summary.remaining_annual_limit"])

    if "service_date" in c and "submitted_on" in c:
        sd = date.fromisoformat(c["service_date"])
        so = date.fromisoformat(c["submitted_on"])
        if (so - sd).days > config["filing_days"]:
            hit("R11", "STANDARD_REVIEW", ["get_claim.submitted_on", "get_claim.service_date"])

    req_docs = facts.get("required_documents", [])
    pres_docs = ex.get("documents_present", [])
    missing = set(req_docs) - set(pres_docs)
    if missing:
        hit("R14", "STANDARD_REVIEW", ["required_documents"])

    if not facts.get("supports"):
        hit("R15", "ESCALATE", ["citations.supports"])

    if facts.get("restricts"):
        hit("R17", "STANDARD_REVIEW", ["citations.restricts"])

    if not raw_hits:
        return "FAST_TRACK", []

    # Calculate highest rank
    top_rank = max(RANK[rt] for _, rt, _ in raw_hits)
    final_route = REV_RANK[top_rank]

    # Deduplicate rule hits
    seen_ids = set()
    deduped_hits: list[RuleHit] = []
    for r_id, rt, ev in raw_hits:
        if r_id not in seen_ids:
            seen_ids.add(r_id)
            deduped_hits.append(RuleHit(rule_id=r_id, route=rt, evidence=ev))

    deduped_hits.sort(key=lambda h: h.rule_id)
    return final_route, deduped_hits


def route(facts: dict[str, Any], cfg: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    """Reference routing function returning (route_name, list_of_rule_ids)."""
    route_name, hits = evaluate_rules(facts, cfg)
    return route_name, [h.rule_id for h in hits]
