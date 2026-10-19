"""Contract integration test comparing live Project A MCP server to case fixtures per SPEC 8 & T12."""

import json
import os
from pathlib import Path

import pytest

from triage.tools.base import ToolError
from triage.tools.fixture_backend import FixtureToolsBackend
from triage.tools.mcp_backend import McpToolsBackend

CASES_DIR = Path("data/cases")


@pytest.mark.integration
def test_live_mcp_matches_fixture_backend_all_cases():
    """Compare live Project A responses to offline fixture responses for all scenario cases."""
    mcp_url = os.environ.get("CLAIMS_MCP_URL")
    mcp_key = os.environ.get("CLAIMS_MCP_KEY", "")

    if not mcp_url:
        pytest.skip("CLAIMS_MCP_URL is unset; skipping MCP live contract integration test.")

    live_backend = McpToolsBackend(url=mcp_url, key=mcp_key)
    fixture_backend = FixtureToolsBackend(CASES_DIR)

    case_files = sorted(CASES_DIR.glob("*.json"))
    assert len(case_files) == 43, f"Expected 43 cases, found {len(case_files)}"

    for cf in case_files:
        with open(cf, "r", encoding="utf-8") as f:
            case_data = json.load(f)

        claim_id = case_data["claim_id"]

        # 1. Test get_claim
        try:
            fixture_claim = fixture_backend.get_claim(claim_id)
            live_claim = live_backend.get_claim(claim_id)
            assert live_claim == fixture_claim, f"get_claim mismatch on {claim_id}"
        except ToolError:
            # If claim missing in fixture, both should fail with NOT_FOUND
            with pytest.raises(ToolError):
                live_backend.get_claim(claim_id)

        # 2. Test get_claim_notes
        fixture_notes = fixture_backend.get_claim_notes(claim_id)
        live_notes = live_backend.get_claim_notes(claim_id)
        assert live_notes == fixture_notes, f"get_claim_notes mismatch on {claim_id}"

        member_id = case_data.get("member", {}).get("member_id")
        plan_id = case_data.get("plan", {}).get("plan_id")
        procedure_code = case_data.get("claim", {}).get("procedure_code")
        service_date = case_data.get("claim", {}).get("service_date", "2026-01-01")
        year = int(service_date.split("-")[0]) if "-" in service_date else 2026

        # 3. Test list_member_claims
        if member_id:
            fixture_mc = fixture_backend.list_member_claims(member_id)
            live_mc = live_backend.list_member_claims(member_id)
            assert live_mc == fixture_mc, f"list_member_claims mismatch on member {member_id}"

            # 4. Test get_member_ytd_summary
            fixture_ytd = fixture_backend.get_member_ytd_summary(member_id, year)
            live_ytd = live_backend.get_member_ytd_summary(member_id, year)
            assert live_ytd == fixture_ytd, f"get_member_ytd_summary mismatch on member {member_id}"

        # 5. Test get_coverage_rule
        if plan_id and procedure_code:
            try:
                fixture_rule = fixture_backend.get_coverage_rule(plan_id, procedure_code)
                live_rule = live_backend.get_coverage_rule(plan_id, procedure_code)
                assert live_rule == fixture_rule, (
                    f"get_coverage_rule mismatch on {plan_id}/{procedure_code}"
                )
            except ToolError:
                with pytest.raises(ToolError):
                    live_backend.get_coverage_rule(plan_id, procedure_code)
