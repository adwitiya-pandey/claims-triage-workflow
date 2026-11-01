"""Fail-safe behavior for graph and LLM execution budgets."""

import logging
import time
from types import SimpleNamespace

from triage.config import TriageConfig
from triage.service import TriageService
from triage.tools.fixture_backend import FixtureToolsBackend


def _service(tmp_path, **config_overrides):
    config = TriageConfig(
        TRIAGE_DB_PATH=str(tmp_path / "triage.db"),
        CHECKPOINT_DB_PATH=str(tmp_path / "checkpoint.db"),
        LLM_MODE="oracle",
        **config_overrides,
    )
    return TriageService(
        config=config,
        tools=FixtureToolsBackend(cases_dir="data/cases"),
        cases_dir="data/cases",
    )


def _assert_budget_fail_safe(result):
    decision = result["decision"]
    assert result["status"] == "completed"
    assert decision["route_recommended"] == "ESCALATE"
    assert decision["route_final"] == "ESCALATE"
    assert any(
        "budget_exhausted" in entry["detail_codes"] for entry in decision["trail"]
    )


def test_recursion_limit_returns_escalation(tmp_path, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="triage.workflow")
    service = _service(tmp_path, MAX_GRAPH_STEPS=1)
    monkeypatch.setattr(logging.getLogger("triage.workflow"), "propagate", True)

    result = service.start_triage("CLM70000001")

    _assert_budget_fail_safe(result)
    assert any(
        "budget_exhausted" in getattr(record, "guardrail_flags", [])
        for record in caplog.records
    )


def test_llm_timeout_returns_escalation(tmp_path):
    class SleepingClient:
        def __init__(self):
            self.models = SimpleNamespace(generate_content=self.generate_content)

        @staticmethod
        def generate_content(**_kwargs):
            time.sleep(0.15)
            return SimpleNamespace(
                text="{}",
                usage_metadata=SimpleNamespace(
                    prompt_token_count=10, candidates_token_count=5
                ),
            )

    config = TriageConfig(
        TRIAGE_DB_PATH=str(tmp_path / "triage.db"),
        CHECKPOINT_DB_PATH=str(tmp_path / "checkpoint.db"),
        LLM_MODE="live",
        GEMINI_API_KEY="test-key",
        RUN_TIMEOUT_SECONDS=0.1,
    )
    service = TriageService(
        config=config,
        tools=FixtureToolsBackend(cases_dir="data/cases"),
        cases_dir="data/cases",
        fake_client=SleepingClient(),
    )

    result = service.start_triage("CLM70000001")

    _assert_budget_fail_safe(result)


def test_llm_call_cap_returns_escalation(tmp_path):
    service = _service(tmp_path, MAX_LLM_CALLS_PER_RUN=1)

    result = service.start_triage("CLM70000001")

    _assert_budget_fail_safe(result)
