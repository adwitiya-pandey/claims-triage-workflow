"""Graph node implementations per SPEC 5, 8, 10, 11, 12, 13."""

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from triage.injection import injection_flags
from triage.llm import format_extract_user_prompt, generate_json, load_prompt
from triage.models import Extraction, validate_claim_id
from triage.sanitize import sanitize_text
from triage.tools.base import ClaimsTools, ToolError

DEFAULT_CASES_DIR = Path("data/cases")
REQUIRED_DOCS_PATH = Path("data/rules/required_documents.json")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def make_trail_entry(
    node: str, outcome: str, detail_codes: list[str] | None = None
) -> dict[str, Any]:
    """Helper to build an audit trail entry."""
    now = _now_iso()
    return {
        "node": node,
        "started_at": now,
        "ended_at": now,
        "outcome": outcome,
        "detail_codes": detail_codes or [],
    }


def make_nodes(
    deps: dict[str, Any] | None = None,
) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    """Factory returning graph node functions with injected dependencies."""
    d = deps or {}
    tools: ClaimsTools | None = d.get("tools")
    llm_fn: Callable = d.get("llm_fn", generate_json)
    llm_mode: str | None = d.get("llm_mode")
    cases_dir = Path(d.get("cases_dir", DEFAULT_CASES_DIR))
    sleep_fn: Callable[[float], None] = d.get("sleep_fn", time.sleep)

    # Load required documents map once
    req_docs_map: dict[str, list[str]] = {}
    if REQUIRED_DOCS_PATH.exists():
        with open(REQUIRED_DOCS_PATH, "r", encoding="utf-8") as f:
            req_docs_map = json.load(f)

    # 1. Intake node
    def intake(state: dict[str, Any]) -> dict[str, Any]:
        claim_id = state.get("claim_id", "")
        validate_claim_id(claim_id)

        raw_text = state.get("raw_text")
        if raw_text is None:
            # Load from case store if available
            raw_text = ""
            if cases_dir.exists():
                for cf in cases_dir.glob("*.json"):
                    try:
                        with open(cf, "r", encoding="utf-8") as f:
                            c_data = json.load(f)
                        if c_data.get("claim_id") == claim_id:
                            raw_text = c_data.get("submission_text", "")
                            break
                    except (json.JSONDecodeError, OSError):
                        pass

        # Scan original text for injection before sanitizing
        found_flags = injection_flags(raw_text)
        current_flags = list(set(state.get("injection_flags", []) + found_flags))

        # Sanitize text
        clean_text, _ = sanitize_text(raw_text)

        detail_codes = ["FLAGGED"] if found_flags else []
        return {
            "raw_text": raw_text,
            "clean_text": clean_text,
            "injection_flags": current_flags,
            "trail": [make_trail_entry("intake", "ok", detail_codes)],
        }

    # 2. Extract node
    def extract(state: dict[str, Any]) -> dict[str, Any]:
        if state.get("extraction_failed"):
            return {
                "trail": [make_trail_entry("extract", "skipped", ["PREVIOUS_FAILURE"])],
            }

        clean_text = state.get("clean_text", "")
        user_prompt = format_extract_user_prompt(clean_text)
        sys_prompt = load_prompt("extract_v1")
        schema = Extraction.model_json_schema()

        extraction_obj: Extraction | None = None
        max_attempts = 2  # 1 initial call + 1 retry

        for attempt in range(1, max_attempts + 1):
            try:
                res = llm_fn(
                    node="extract",
                    system_prompt=sys_prompt,
                    user_content=user_prompt,
                    schema=schema,
                    prompt_version="extract_v1",
                    mode=llm_mode,
                )
                extraction_obj = Extraction.model_validate_json(res.text)
                break
            except (ValidationError, json.JSONDecodeError):
                if attempt == max_attempts:
                    break

        if extraction_obj is None:
            return {
                "extraction": None,
                "extraction_failed": True,
                "trail": [
                    make_trail_entry("extract", "error", ["RETRY_FAILED", "EXTRACTION_FAILED"])
                ],
            }

        return {
            "extraction": extraction_obj.model_dump(mode="json"),
            "extraction_failed": False,
            "trail": [make_trail_entry("extract", "ok")],
        }

    # 3. Fetch facts node
    def fetch_facts(state: dict[str, Any]) -> dict[str, Any]:
        if not tools:
            raise RuntimeError("ClaimsTools backend required for fetch_facts")

        claim_id = state.get("claim_id", "")
        claim_missing = False
        tool_error: str | None = None
        claim_data: dict[str, Any] = {}

        # 3.1 get_claim with RATE_LIMITED retry
        delays = [1.0, 2.0, 4.0]
        for attempt in range(len(delays) + 1):
            try:
                claim_data = tools.get_claim(claim_id)
                break
            except ToolError as exc:
                if exc.code == "NOT_FOUND":
                    claim_missing = True
                    break
                if exc.code == "RATE_LIMITED":
                    if attempt < len(delays):
                        sleep_fn(delays[attempt])
                        continue
                    tool_error = "RATE_LIMITED"
                    break
                tool_error = exc.code
                break
            except (OSError, TimeoutError, RuntimeError):
                tool_error = "INTERNAL"
                break

        if claim_missing or tool_error:
            detail = ["CLAIM_NOT_FOUND"] if claim_missing else [f"TOOL_{tool_error}"]
            return {
                "facts": {},
                "claim_missing": claim_missing,
                "tool_error": tool_error,
                "trail": [make_trail_entry("fetch_facts", "error", detail)],
            }

        member_id = claim_data["member_id"]
        plan_id = claim_data["plan_id"]
        procedure_code = claim_data["procedure_code"]
        service_date = claim_data["service_date"]
        service_year = int(service_date.split("-")[0]) if "-" in service_date else 2026

        # 3.2 list_member_claims & duplicates
        member_claims = tools.list_member_claims(member_id, limit=50)
        duplicates = [
            c
            for c in member_claims.get("items", [])
            if c.get("procedure_code") == procedure_code
            and c.get("service_date") == service_date
            and c.get("claim_id") != claim_id
        ]

        # 3.3 get_coverage_rule
        coverage_rule: dict[str, Any] | None = None
        try:
            coverage_rule = tools.get_coverage_rule(plan_id, procedure_code)
        except ToolError as exc:
            if exc.code == "NOT_FOUND":
                coverage_rule = None
            else:
                tool_error = exc.code

        # 3.4 get_member_ytd_summary
        ytd_summary: dict[str, Any] = {}
        try:
            ytd_summary = tools.get_member_ytd_summary(member_id, service_year)
        except ToolError:
            ytd_summary = {}

        # 3.5 get_claim_notes and scan for injection
        notes_resp = tools.get_claim_notes(claim_id, limit=10)
        claim_notes = notes_resp.get("notes", [])
        current_flags = list(state.get("injection_flags", []))

        for n in claim_notes:
            n_flags = injection_flags(n.get("text", ""))
            for f in n_flags:
                if f not in current_flags:
                    current_flags.append(f)

        required_documents = req_docs_map.get(procedure_code, ["claim_form", "itemized_bill"])

        facts_bundle = {
            "claim": claim_data,
            "member_claims": member_claims.get("items", []),
            "duplicates": duplicates,
            "coverage_rule": coverage_rule,
            "member_ytd": ytd_summary,
            "remaining_annual_limit": ytd_summary.get("remaining_annual_limit"),
            "claim_notes": claim_notes,
            "required_documents": required_documents,
        }

        return {
            "facts": facts_bundle,
            "claim_missing": False,
            "tool_error": tool_error,
            "injection_flags": current_flags,
            "trail": [make_trail_entry("fetch_facts", "ok")],
        }

    return {
        "intake": intake,
        "extract": extract,
        "fetch_facts": fetch_facts,
    }
