"""In-memory fixture backend for claims tools per SPEC 8."""

import json
from pathlib import Path
from typing import Any

from triage.tools.base import ToolError

DEFAULT_CASES_DIR = Path("data/cases")


class FixtureToolsBackend:
    """Read-only claims tools backed by synthetic scenario cases."""

    def __init__(self, cases_dir: Path | str | None = None):
        self.cases_dir = Path(cases_dir) if cases_dir else DEFAULT_CASES_DIR
        self.plans: dict[str, dict[str, Any]] = {}
        self.members: dict[str, dict[str, Any]] = {}
        self.providers: dict[str, dict[str, Any]] = {}
        self.coverage_rules: dict[tuple[str, str], dict[str, Any]] = {}
        self.claims: dict[str, dict[str, Any]] = {}
        self.notes: dict[str, list[dict[str, Any]]] = {}
        self.member_claims: dict[str, list[dict[str, Any]]] = {}

        self._load_all_cases()

    def _load_all_cases(self) -> None:
        """Load and index all scenario case JSON files."""
        if not self.cases_dir.exists():
            return

        for case_file in sorted(self.cases_dir.glob("*.json")):
            with open(case_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Plan
            plan = data.get("plan")
            if plan and "plan_id" in plan:
                self.plans[plan["plan_id"]] = plan

            # Member
            member = data.get("member")
            if member and "member_id" in member:
                self.members[member["member_id"]] = member
                if member["member_id"] not in self.member_claims:
                    self.member_claims[member["member_id"]] = []

            # Provider
            provider = data.get("provider")
            if provider and "provider_id" in provider:
                self.providers[provider["provider_id"]] = provider

            # Coverage rules
            for rule in data.get("coverage_rules", []):
                key = (rule["plan_id"], rule["procedure_code"])
                self.coverage_rules[key] = rule

            # Primary claim (if not deliberately missing from DB)
            claim = data.get("claim")
            if claim and "claim_id" in claim and not data.get("claim_missing"):
                self.claims[claim["claim_id"]] = claim
                mid = claim.get("member_id")
                if mid:
                    self.member_claims.setdefault(mid, [])
                    if not any(c["claim_id"] == claim["claim_id"] for c in self.member_claims[mid]):
                        self.member_claims[mid].append(claim)

            # Other claims (e.g. for duplicate or limit scenarios)
            for other in data.get("other_claims", []):
                self.claims[other["claim_id"]] = other
                mid = other.get("member_id")
                if mid:
                    self.member_claims.setdefault(mid, [])
                    if not any(c["claim_id"] == other["claim_id"] for c in self.member_claims[mid]):
                        self.member_claims[mid].append(other)

            # Notes
            cid = data.get("claim_id")
            if cid:
                case_notes = data.get("notes", [])
                formatted_notes = []
                for idx, note in enumerate(case_notes, 1):
                    raw_text = note.get("note_text", "")
                    truncated = len(raw_text) > 1000
                    formatted_notes.append(
                        {
                            "note_id": idx,
                            "created_on": note.get("created_on", "2026-05-01"),
                            "author_type": note.get("author_type", "CLAIMANT"),
                            "text": raw_text[:1000],
                            "truncated": truncated,
                        }
                    )
                self.notes[cid] = formatted_notes

    def get_claim(self, claim_id: str) -> dict:
        """Fetch joined claim record."""
        claim = self.claims.get(claim_id)
        if not claim:
            raise ToolError("NOT_FOUND", f"Claim {claim_id} not found")

        provider = self.providers.get(claim["provider_id"], {})
        member = self.members.get(claim["member_id"], {})

        return {
            "claim_id": claim["claim_id"],
            "member_id": claim["member_id"],
            "plan_id": member.get("plan_id", "PLN001"),
            "provider_id": claim["provider_id"],
            "provider_network_status": provider.get("network_status", "IN_NETWORK"),
            "procedure_code": claim["procedure_code"],
            "diagnosis_code": claim.get("diagnosis_code", "Z00.00"),
            "service_date": claim["service_date"],
            "submitted_on": claim["submitted_on"],
            "billed_amount": float(claim["billed_amount"]),
            "allowed_amount": float(claim["allowed_amount"])
            if claim.get("allowed_amount") is not None
            else None,
            "paid_amount": float(claim["paid_amount"])
            if claim.get("paid_amount") is not None
            else None,
            "status": claim["status"],
        }

    def list_member_claims(
        self,
        member_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """List claims for member newest first."""
        if member_id not in self.members:
            raise ToolError("NOT_FOUND", f"Member {member_id} not found")

        claims_list = list(self.member_claims.get(member_id, []))
        if status:
            claims_list = [c for c in claims_list if c["status"] == status]

        # Sort service_date descending, then claim_id
        claims_list.sort(key=lambda c: (c["service_date"], c["claim_id"]), reverse=True)

        total = len(claims_list)
        paged = claims_list[offset : offset + limit]

        items = [
            {
                "claim_id": c["claim_id"],
                "service_date": c["service_date"],
                "procedure_code": c["procedure_code"],
                "billed_amount": float(c["billed_amount"]),
                "status": c["status"],
            }
            for c in paged
        ]

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": items,
        }

    def get_coverage_rule(self, plan_id: str, procedure_code: str) -> dict:
        """Fetch coverage rule."""
        rule = self.coverage_rules.get((plan_id, procedure_code))
        if not rule:
            raise ToolError("NOT_FOUND", f"Coverage rule not found for {plan_id}/{procedure_code}")

        max_allowed = rule.get("max_allowed")
        return {
            "plan_id": plan_id,
            "procedure_code": procedure_code,
            "covered": bool(rule["covered"]),
            "requires_preauth": bool(rule["requires_preauth"]),
            "max_allowed": float(max_allowed) if max_allowed is not None else None,
        }

    def get_member_ytd_summary(self, member_id: str, year: int) -> dict:
        """Fetch YTD summary and remaining annual limit."""
        member = self.members.get(member_id)
        if not member:
            raise ToolError("NOT_FOUND", f"Member {member_id} not found")

        plan = self.plans.get(member["plan_id"], {})
        annual_limit = float(plan.get("annual_limit", 50000.0))
        deductible = float(plan.get("deductible", 500.0))

        member_claims = self.member_claims.get(member_id, [])
        year_claims = [c for c in member_claims if str(year) in c["service_date"]]

        by_status_map: dict[str, dict[str, Any]] = {}
        total_paid_that_year = 0.0

        for c in year_claims:
            st = c["status"]
            if st not in by_status_map:
                by_status_map[st] = {
                    "status": st,
                    "claim_count": 0,
                    "total_billed": 0.0,
                    "total_paid": 0.0,
                }
            by_status_map[st]["claim_count"] += 1
            by_status_map[st]["total_billed"] += float(c["billed_amount"])
            paid = float(c["paid_amount"]) if c.get("paid_amount") is not None else 0.0
            by_status_map[st]["total_paid"] += paid

            if st == "PAID":
                total_paid_that_year += paid

        remaining = max(0.0, annual_limit - total_paid_that_year)

        return {
            "member_id": member_id,
            "year": year,
            "plan_id": member["plan_id"],
            "annual_limit": annual_limit,
            "deductible": deductible,
            "by_status": list(by_status_map.values()),
            "remaining_annual_limit": remaining,
        }

    def get_claim_notes(self, claim_id: str, limit: int = 10) -> dict:
        """Fetch claim notes."""
        notes = self.notes.get(claim_id, [])
        return {
            "claim_id": claim_id,
            "untrusted": True,
            "notes": notes[:limit],
        }
