"""Unit tests for McpToolsBackend per T12, SPEC 8, and SPEC S4."""

import json
from typing import Any

import pytest

from triage.tools.base import ClaimsTools, ToolError
from triage.tools.mcp_backend import McpToolsBackend


class FakeTextContent:
    def __init__(self, text: str):
        self.text = text


class FakeCallResult:
    def __init__(self, text: str, is_error: bool = False):
        self.content = [FakeTextContent(text)]
        self.is_error = is_error


class FakeMcpClient:
    def __init__(self, responses: dict[str, Any] | None = None):
        self.responses = responses or {}
        self.call_history: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, args: dict) -> FakeCallResult:
        self.call_history.append((name, args))
        res = self.responses.get(name)
        if isinstance(res, Exception):
            raise res
        if callable(res):
            return res(args)
        if isinstance(res, FakeCallResult):
            return res
        # Default success
        return FakeCallResult(json.dumps({"status": "ok"}))


def test_mcp_tools_backend_implements_protocol_and_has_no_write_methods():
    """Verify backend conforms to ClaimsTools and has no write methods per SPEC S4."""
    backend = McpToolsBackend(url="http://fake:8000/mcp", key="fake-key")
    assert isinstance(backend, ClaimsTools)

    # Security check: absolutely no write methods may exist
    forbidden_words = ["create", "update", "delete", "write", "insert", "save", "drop", "modify"]
    for attr in dir(backend):
        if not attr.startswith("_"):
            for bad in forbidden_words:
                assert bad not in attr.lower(), f"Forbidden method name found on backend: {attr}"


def test_get_claim_success():
    client = FakeMcpClient(
        {
            "get_claim": FakeCallResult(
                json.dumps({"claim_id": "CLM70000001", "member_id": "M700001"})
            )
        }
    )
    backend = McpToolsBackend(url="http://fake", key="key", client_override=client)

    result = backend.get_claim("CLM70000001")
    assert result["claim_id"] == "CLM70000001"
    assert client.call_history == [("get_claim", {"claim_id": "CLM70000001"})]


def test_list_member_claims_success():
    items = [{"claim_id": "CLM70000001"}]
    client = FakeMcpClient(
        {"list_member_claims": FakeCallResult(json.dumps({"items": items, "total": 1}))}
    )
    backend = McpToolsBackend(url="http://fake", key="key", client_override=client)

    result = backend.list_member_claims("M700001", status="SUBMITTED", limit=20, offset=5)
    assert result["items"] == items
    assert client.call_history == [
        (
            "list_member_claims",
            {"member_id": "M700001", "status": "SUBMITTED", "limit": 20, "offset": 5},
        )
    ]


def test_get_coverage_rule_success():
    client = FakeMcpClient(
        {
            "get_coverage_rule": FakeCallResult(
                json.dumps({"covered": True, "requires_preauth": False})
            )
        }
    )
    backend = McpToolsBackend(url="http://fake", key="key", client_override=client)

    result = backend.get_coverage_rule("PLN001", "PX0001")
    assert result["covered"] is True
    assert client.call_history == [
        ("get_coverage_rule", {"plan_id": "PLN001", "procedure_code": "PX0001"})
    ]


def test_get_member_ytd_summary_success():
    client = FakeMcpClient(
        {
            "get_member_ytd_summary": FakeCallResult(
                json.dumps({"member_id": "M700001", "total_paid_ytd": 1200.0})
            )
        }
    )
    backend = McpToolsBackend(url="http://fake", key="key", client_override=client)

    result = backend.get_member_ytd_summary("M700001", 2026)
    assert result["total_paid_ytd"] == 1200.0
    assert client.call_history == [
        ("get_member_ytd_summary", {"member_id": "M700001", "year": 2026})
    ]


def test_get_claim_notes_success():
    notes = [{"note_id": "N01", "text": "Claim received."}]
    client = FakeMcpClient(
        {"get_claim_notes": FakeCallResult(json.dumps({"notes": notes, "untrusted": True}))}
    )
    backend = McpToolsBackend(url="http://fake", key="key", client_override=client)

    result = backend.get_claim_notes("CLM70000001", limit=5)
    assert result["notes"] == notes
    assert client.call_history == [("get_claim_notes", {"claim_id": "CLM70000001", "limit": 5})]


def test_error_mapping_not_found():
    client = FakeMcpClient(
        {"get_claim": FakeCallResult("NOT_FOUND: claim CLM99999999 does not exist", is_error=True)}
    )
    backend = McpToolsBackend(url="http://fake", key="key", client_override=client)

    with pytest.raises(ToolError) as exc_info:
        backend.get_claim("CLM99999999")
    assert exc_info.value.code == "NOT_FOUND"


def test_error_mapping_forbidden_tool():
    client = FakeMcpClient({"get_claim": FakeCallResult("FORBIDDEN_TOOL", is_error=True)})
    backend = McpToolsBackend(url="http://fake", key="key", client_override=client)

    with pytest.raises(ToolError) as exc_info:
        backend.get_claim("CLM70000001")
    assert exc_info.value.code == "FORBIDDEN_TOOL"


def test_retry_on_rate_limited():
    calls = 0
    sleeps = []

    def handle_rate_limit(args):
        nonlocal calls
        calls += 1
        if calls < 3:
            return FakeCallResult("RATE_LIMITED: try again later", is_error=True)
        return FakeCallResult(json.dumps({"claim_id": "CLM70000001"}))

    client = FakeMcpClient({"get_claim": handle_rate_limit})
    backend = McpToolsBackend(
        url="http://fake",
        key="key",
        client_override=client,
        sleep_fn=lambda s: sleeps.append(s),
    )

    res = backend.get_claim("CLM70000001")
    assert res["claim_id"] == "CLM70000001"
    assert calls == 3
    assert sleeps == [1.0, 2.0]


def test_rate_limited_exhaustion_raises_tool_error():
    calls = 0
    sleeps = []

    def handle_rate_limit(args):
        nonlocal calls
        calls += 1
        return FakeCallResult("RATE_LIMITED: persistent", is_error=True)

    client = FakeMcpClient({"get_claim": handle_rate_limit})
    backend = McpToolsBackend(
        url="http://fake",
        key="key",
        client_override=client,
        sleep_fn=lambda s: sleeps.append(s),
    )

    with pytest.raises(ToolError) as exc_info:
        backend.get_claim("CLM70000001")
    assert exc_info.value.code == "RATE_LIMITED"
    assert calls == 4
    assert sleeps == [1.0, 2.0, 4.0]


def test_http_401_maps_to_unauthenticated():
    class Fake401Error(Exception):
        status_code = 401

    client = FakeMcpClient({"get_claim": Fake401Error("HTTP 401 Unauthorized")})
    backend = McpToolsBackend(url="http://fake", key="key", client_override=client)

    with pytest.raises(ToolError) as exc_info:
        backend.get_claim("CLM70000001")
    assert exc_info.value.code == "UNAUTHENTICATED"
