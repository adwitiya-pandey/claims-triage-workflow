"""Graph node implementations per SPEC 5, 8, 10, 11, 12, 13."""

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.types import interrupt
from pydantic import ValidationError

from triage.config import get_settings
from triage.injection import injection_flags
from triage.llm import (
    format_assess_user_prompt,
    format_extract_user_prompt,
    generate_json,
    load_prompt,
)
from triage.models import (
    Assessment,
    Decision,
    Extraction,
    ReviewAction,
    validate_claim_id,
)
from triage.retrieval import compute_corpus_sha256, retrieve
from triage.rules import evaluate_rules
from triage.sanitize import sanitize_text
from triage.store import save_decision
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
    """Factory returning all graph node functions with injected dependencies."""
    d = deps or {}
    tools: ClaimsTools | None = d.get("tools")
    llm_fn: Callable = d.get("llm_fn", generate_json)
    llm_mode: str | None = d.get("llm_mode")
    cases_dir = Path(d.get("cases_dir", DEFAULT_CASES_DIR))
    sleep_fn: Callable[[float], None] = d.get("sleep_fn", time.sleep)
    interrupt_fn: Callable = d.get("interrupt_fn", interrupt)
    db_path: Path | str = d.get("db_path", get_settings().TRIAGE_DB_PATH)

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

        found_flags = injection_flags(raw_text)
        current_flags = list(set(state.get("injection_flags", []) + found_flags))
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
        max_attempts = 2

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

        member_claims = tools.list_member_claims(member_id, limit=50)
        duplicates = [
            c
            for c in member_claims.get("items", [])
            if c.get("procedure_code") == procedure_code
            and c.get("service_date") == service_date
            and c.get("claim_id") != claim_id
        ]

        coverage_rule: dict[str, Any] | None = None
        try:
            coverage_rule = tools.get_coverage_rule(plan_id, procedure_code)
        except ToolError as exc:
            if exc.code == "NOT_FOUND":
                coverage_rule = None
            else:
                tool_error = exc.code

        ytd_summary: dict[str, Any] = {}
        try:
            ytd_summary = tools.get_member_ytd_summary(member_id, service_year)
        except ToolError:
            ytd_summary = {}

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

    # 4. Retrieve node
    def retrieve_node(state: dict[str, Any]) -> dict[str, Any]:
        if state.get("extraction_failed") or state.get("claim_missing") or state.get("tool_error"):
            return {
                "chunks": [],
                "trail": [make_trail_entry("retrieve", "skipped")],
            }

        ex = state.get("extraction") or {}
        facts = state.get("facts") or {}
        claim = facts.get("claim") or {}

        proc_desc = ex.get("procedure_description", "")
        proc_code = claim.get("procedure_code") or ex.get("procedure_code")
        query = f"{proc_desc} {proc_code}".strip() if proc_code else proc_desc

        top_k = get_settings().TOP_K
        chunks = retrieve(query=query, procedure_code=proc_code, top_k=top_k)

        return {
            "chunks": chunks,
            "trail": [make_trail_entry("retrieve", "ok")],
        }

    # 5. Assess node
    def assess_node(state: dict[str, Any]) -> dict[str, Any]:
        if state.get("extraction_failed") or state.get("claim_missing") or state.get("tool_error"):
            return {
                "assessment": None,
                "trail": [make_trail_entry("assess", "skipped")],
            }

        ex = state.get("extraction") or {}
        facts = state.get("facts") or {}
        chunks = state.get("chunks") or []

        user_prompt = format_assess_user_prompt(ex, facts, chunks)
        sys_prompt = load_prompt("assess_v1")
        schema = Assessment.model_json_schema()

        res = llm_fn(
            node="assess",
            system_prompt=sys_prompt,
            user_content=user_prompt,
            schema=schema,
            prompt_version="assess_v1",
            mode=llm_mode,
        )

        try:
            raw_ass = Assessment.model_validate_json(res.text)
        except (ValidationError, json.JSONDecodeError):
            return {
                "assessment": None,
                "trail": [make_trail_entry("assess", "error", ["ASSESSMENT_PARSE_FAILED"])],
            }

        # Deterministic citation checks (SPEC 10)
        retrieved_map = {c["chunk_id"]: c["text"] for c in chunks}
        valid_clauses = []
        dropped_count = 0

        for item in raw_ass.clauses:
            if item.chunk_id not in retrieved_map:
                dropped_count += 1
                continue

            chunk_text = retrieved_map[item.chunk_id]
            if item.effect in ("supports", "restricts"):
                if len(item.quote) < 20:
                    dropped_count += 1
                    continue
                norm_quote = " ".join(item.quote.lower().split())
                norm_chunk = " ".join(chunk_text.lower().split())
                if norm_quote not in norm_chunk:
                    dropped_count += 1
                    continue

            valid_clauses.append(item.model_dump(mode="json"))

        ass_dict = {
            "summary": raw_ass.summary[:700],
            "clauses": valid_clauses,
            "missing_items": raw_ass.missing_items[:8],
            "confidence": raw_ass.confidence,
        }

        detail_codes = [f"DROPPED_{dropped_count}"] if dropped_count > 0 else []
        return {
            "assessment": ass_dict,
            "dropped_citations": dropped_count,
            "trail": [make_trail_entry("assess", "ok", detail_codes)],
        }

    # 6. Decide node
    def decide_node(state: dict[str, Any]) -> dict[str, Any]:
        facts = state.get("facts") or {}
        ex = state.get("extraction") or state.get("extracted") or {}
        ass = state.get("assessment") or {}
        clauses = ass.get("clauses", [])

        supports = [c for c in clauses if c.get("effect") == "supports"]
        restricts = [c for c in clauses if c.get("effect") == "restricts"]

        facts_for_rules = {
            "claim": facts.get("claim", {}),
            "extracted": ex,
            "coverage_rule": facts.get("coverage_rule"),
            "duplicates": facts.get("duplicates", []),
            "remaining_annual_limit": facts.get("remaining_annual_limit"),
            "required_documents": facts.get("required_documents", []),
            "supports": supports,
            "restricts": restricts,
            "injection_flags": state.get("injection_flags", []),
            "claim_missing": state.get("claim_missing", False),
            "extraction_failed": state.get("extraction_failed", False),
            "tool_error": state.get("tool_error"),
            "confidence": ex.get("confidence", 1.0) if ex else 1.0,
        }

        settings = get_settings()
        cfg = {
            "high_amount": settings.HIGH_AMOUNT_THRESHOLD,
            "filing_days": settings.TIMELY_FILING_DAYS,
            "min_conf": settings.MIN_EXTRACT_CONFIDENCE,
        }
        route_rec, rule_hits = evaluate_rules(facts_for_rules, cfg)

        hits_dicts = [h.model_dump(mode="json") for h in rule_hits]
        detail_codes = [h["rule_id"] for h in hits_dicts]
        return {
            "route": route_rec,
            "rule_hits": hits_dicts,
            "trail": [make_trail_entry("decide", "ok", detail_codes)],
        }

    # 7. Review node
    def review_node(state: dict[str, Any]) -> dict[str, Any]:
        facts = state.get("facts") or {}
        claim = facts.get("claim") or {}
        ex = state.get("extraction") or {}
        ass = state.get("assessment") or {}

        # Build review packet containing no raw claim or note text (SPEC 12)
        packet = {
            "claim_id": state.get("claim_id"),
            "route_recommended": state.get("route"),
            "rule_hits": state.get("rule_hits", []),
            "citations": ass.get("clauses", []),
            "summary": ass.get("summary", ""),
            "extraction": ex,
            "facts_digest": {
                "billed_amount": claim.get("billed_amount"),
                "status": claim.get("status"),
                "procedure_code": claim.get("procedure_code"),
                "provider_network_status": claim.get("provider_network_status"),
                "service_date": claim.get("service_date"),
                "submitted_on": claim.get("submitted_on"),
            },
            "missing_items": ass.get("missing_items", []),
            "injection_flags": state.get("injection_flags", []),
        }

        # Interrupt execution and wait for human review action
        resume_val = interrupt_fn(packet)

        # Validate human action
        action_obj = ReviewAction.model_validate(resume_val)
        return {
            "review": action_obj.model_dump(mode="json"),
            "trail": [make_trail_entry("review", "ok", [action_obj.action])],
        }

    # 8. Finalize node
    def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
        route_rec = state.get("route", "ESCALATE")
        review_data = state.get("review")

        if review_data:
            if review_data.get("action") == "ESCALATE_FURTHER":
                route_final = "ESCALATE"
            else:
                route_final = route_rec
        else:
            route_final = route_rec

        facts = state.get("facts") or {}
        claim = facts.get("claim") or {}
        ex = state.get("extraction")
        ass = state.get("assessment") or {}

        finalize_trail = make_trail_entry("finalize", "ok")
        full_trail = list(state.get("trail", [])) + [finalize_trail]

        decision_record = {
            "claim_id": state["claim_id"],
            "version": int(state.get("version", 1)),
            "route_recommended": route_rec,
            "route_final": route_final,
            "reasons": state.get("rule_hits", []),
            "citations": ass.get("clauses", []),
            "summary": ass.get("summary", ""),
            "missing_items": ass.get("missing_items", []),
            "extraction": ex,
            "facts_digest": {
                "billed_amount": claim.get("billed_amount"),
                "status": claim.get("status"),
                "procedure_code": claim.get("procedure_code"),
                "provider_network_status": claim.get("provider_network_status"),
            },
            "review": review_data,
            "versions": {
                "graph": "1",
                "rules": "1",
                "prompt_extract": "extract_v1",
                "prompt_assess": "assess_v1",
                "model": get_settings().GEMINI_MODEL,
                "corpus_sha256": compute_corpus_sha256(),
            },
            "llm_usage": {"input_tokens": 0, "output_tokens": 0, "calls": 2},
            "trail": full_trail,
            "created_at": _now_iso(),
        }

        # Validate decision structure
        Decision.model_validate(decision_record)

        # Persist decision to SQLite
        save_decision(db_path, decision_record)

        return {
            "decision": decision_record,
            "trail": [finalize_trail],
        }

    return {
        "intake": intake,
        "extract": extract,
        "fetch_facts": fetch_facts,
        "retrieve": retrieve_node,
        "assess": assess_node,
        "decide": decide_node,
        "review": review_node,
        "finalize": finalize_node,
    }
