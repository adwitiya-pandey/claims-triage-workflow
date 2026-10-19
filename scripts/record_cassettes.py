"""Script to record live Gemini LLM responses for all 43 scenario cases per SPEC 10, SPEC 18, and T15/T16."""

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from triage.config import get_settings
from triage.llm import (
    format_assess_user_prompt,
    format_extract_user_prompt,
    generate_json,
    load_prompt,
)
from triage.models import Assessment, Extraction
from triage.retrieval import retrieve_clauses
from triage.tools.base import ToolError
from triage.tools.fixture_backend import FixtureToolsBackend

CASES_DIR = Path("data/cases")


def record_all_cassettes(
    cassette_path: Path | str | None = None,
    delay_between_calls: float = 4.0,
    allow_synthetic_fallback: bool = False,
) -> None:
    """Record LLM responses (extract + assess) for all scenario cases into cassette.jsonl."""
    # Load .env if present
    load_dotenv()

    settings = get_settings()
    c_path = Path(cassette_path or settings.CASSETTE_PATH)
    c_path.parent.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        if not allow_synthetic_fallback:
            raise RuntimeError(
                "GEMINI_API_KEY is not set in the environment or .env file. "
                "Provide a valid key or enable synthetic seeding."
            )
        print(
            "GEMINI_API_KEY not set; seeding deterministic recorded responses from gold fixtures..."
        )
        mode = "oracle"
    else:
        mode = "record"

    case_files = sorted(CASES_DIR.glob("*.json"))
    total_cases = len(case_files)
    print(f"Starting cassette recording for {total_cases} cases using mode='{mode}'...")
    print(f"Target cassette file: {c_path}")

    # Remove existing cassette file if re-recording fresh
    if c_path.exists():
        c_path.unlink()

    tools = FixtureToolsBackend(CASES_DIR)
    extract_sys = load_prompt("extract_v1")
    assess_sys = load_prompt("assess_v1")
    extract_schema = Extraction.model_json_schema()
    assess_schema = Assessment.model_json_schema()

    total_calls = 0

    for idx, cf in enumerate(case_files, 1):
        with open(cf, "r", encoding="utf-8") as f:
            case_data = json.load(f)

        case_id = case_data.get("case_id", cf.stem)
        claim_id = case_data["claim_id"]
        sub_text = case_data.get("submission_text", "")

        print(f"[{idx}/{total_cases}] Processing {case_id} ({claim_id})...")

        # 1. Record Extract Call
        extract_user = format_extract_user_prompt(sub_text)
        print(f"  -> Recording extract for {case_id}...")
        try:
            ex_res = generate_json(
                node="extract",
                system_prompt=extract_sys,
                user_content=extract_user,
                schema=extract_schema,
                prompt_version="extract_v1",
                mode=mode,
                cassette_path=c_path,
            )
            total_calls += 1
            if mode == "record":
                time.sleep(delay_between_calls)
        except Exception as exc:
            print(f"  [ERROR] Extract call failed on {case_id}: {exc}")
            raise

        # Prepare facts and retrieved clauses for Assess Call
        try:
            ex_data = json.loads(ex_res.text)
        except json.JSONDecodeError:
            ex_data = case_data.get("gold_extraction", {})

        try:
            claim_row = tools.get_claim(claim_id)
        except ToolError:
            claim_row = case_data.get("claim", {})

        plan_id = claim_row.get("plan_id", "PLN001")
        proc_code = claim_row.get("procedure_code", "PX0001")

        try:
            cov_rule = tools.get_coverage_rule(plan_id, proc_code)
        except ToolError:
            cov_rule = None

        year = int(claim_row.get("service_date", "2026-01-01").split("-")[0])
        try:
            ytd = tools.get_member_ytd_summary(claim_row.get("member_id", ""), year)
        except ToolError:
            ytd = {}

        try:
            notes_resp = tools.get_claim_notes(claim_id)
            notes = notes_resp.get("notes", [])
        except ToolError:
            notes = []

        facts_bundle: dict[str, Any] = {
            "claim": claim_row,
            "coverage_rule": cov_rule,
            "member_ytd": ytd,
            "remaining_annual_limit": ytd.get("remaining_annual_limit"),
            "claim_notes": notes,
        }

        desc = ex_data.get("procedure_description", proc_code)
        chunks = retrieve_clauses(desc, proc_code)

        # 2. Record Assess Call
        assess_user = format_assess_user_prompt(ex_data, facts_bundle, chunks)
        print(f"  -> Recording assess for {case_id}...")
        try:
            generate_json(
                node="assess",
                system_prompt=assess_sys,
                user_content=assess_user,
                schema=assess_schema,
                prompt_version="assess_v1",
                mode=mode,
                cassette_path=c_path,
            )
            total_calls += 1
            if mode == "record":
                time.sleep(delay_between_calls)
        except Exception as exc:
            print(f"  [ERROR] Assess call failed on {case_id}: {exc}")
            raise

    print(f"\nRecording completed successfully! Total LLM interactions: {total_calls}")
    print(f"Cassettes written to {c_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Record live Gemini cassettes for claims triage")
    parser.add_argument(
        "--cassette-path",
        default="data/cassettes/cassette.jsonl",
        help="Target cassette path",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=4.0,
        help="Pacing delay in seconds between API calls to honor rate limits (default: 4.0)",
    )
    parser.add_argument(
        "--allow-synthetic-fallback",
        action="store_true",
        help="Allow deterministic seeding if GEMINI_API_KEY is not configured",
    )
    args = parser.parse_args()

    record_all_cassettes(
        cassette_path=args.cassette_path,
        delay_between_calls=args.delay,
        allow_synthetic_fallback=args.allow_synthetic_fallback,
    )


if __name__ == "__main__":
    main()
