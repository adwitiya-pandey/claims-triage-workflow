"""Tests for configuration management and secret safety."""

import pytest
from pydantic import ValidationError

from triage.config import Settings


def test_default_config():
    env = {
        "LLM_MODE": "replay",
        "CLAIMS_TOOLS_BACKEND": "fixture",
    }
    s = Settings(**env)
    assert s.GEMINI_MODEL == "gemini-3.8-flash"
    assert s.LLM_MODE == "replay"
    assert s.CASSETTE_PATH == "data/cassettes/cassette.jsonl"
    assert s.CLAIMS_TOOLS_BACKEND == "fixture"
    assert s.HIGH_AMOUNT_THRESHOLD == 10000.0
    assert s.TIMELY_FILING_DAYS == 90
    assert s.MIN_EXTRACT_CONFIDENCE == 0.7
    assert s.TOP_K == 4
    assert s.TRIAGE_DB_PATH == "data/triage.db"
    assert s.CHECKPOINT_DB_PATH == "data/checkpoint.db"
    assert s.API_HOST == "127.0.0.1"
    assert s.API_PORT == 8800
    assert s.LOG_LEVEL == "INFO"


def test_live_mode_requires_gemini_api_key():
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        Settings(LLM_MODE="live", GEMINI_API_KEY=None)

    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        Settings(LLM_MODE="record", GEMINI_API_KEY="")


def test_mcp_backend_requires_endpoint():
    with pytest.raises(ValueError, match="MCP"):
        Settings(CLAIMS_TOOLS_BACKEND="mcp", CLAIMS_MCP_URL=None, CLAIMS_MCP_STDIO_CMD=None)


def test_secrets_never_echoed_in_repr_or_str():
    secret_gemini = "AIzaSySuperSecretKey12345"
    secret_mcp = "secret-mcp-key-9999"
    secret_triage = "triage-admin-token-7777"

    s = Settings(
        GEMINI_API_KEY=secret_gemini,
        CLAIMS_TOOLS_BACKEND="mcp",
        CLAIMS_MCP_URL="http://localhost:8000/mcp",
        CLAIMS_MCP_KEY=secret_mcp,
        TRIAGE_API_KEY=secret_triage,
    )

    s_str = str(s)
    s_repr = repr(s)

    for secret in [secret_gemini, secret_mcp, secret_triage]:
        assert secret not in s_str
        assert secret not in s_repr


def test_config_error_no_secret():
    secret_key = "AIzaSySecretShouldNeverAppear"
    try:
        # Pass an invalid port to trigger validation failure
        Settings(
            GEMINI_API_KEY=secret_key,
            API_PORT="not_a_number",
        )
    except (ValidationError, ValueError) as exc:
        assert secret_key not in str(exc)
