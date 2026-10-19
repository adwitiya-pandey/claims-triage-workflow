"""Tests for structured JSON logging and security guarantees (S7)."""

import json
import logging
from io import StringIO

from triage.logging_setup import JSONFormatter, log_event


def test_json_formatter_fields():
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    logger = logging.getLogger("test_structured_logger")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)

    log_event(
        logger,
        level=logging.INFO,
        claim_id="CLM12345678",
        thread_id="triage-CLM12345678-v1",
        node="intake",
        duration_ms=12.5,
        outcome="ok",
        rule_ids=["R01"],
        extra_info="node finished",
    )

    output = stream.getvalue().strip()
    record = json.loads(output)

    assert record["claim_id"] == "CLM12345678"
    assert record["thread_id"] == "triage-CLM12345678-v1"
    assert record["node"] == "intake"
    assert record["duration_ms"] == 12.5
    assert record["outcome"] == "ok"
    assert record["rule_ids"] == ["R01"]
    assert "timestamp" in record
    assert record["level"] == "INFO"


def test_logs_have_no_text():
    """Verify that raw claim text, prompt content, or notes cannot leak into logs (S7)."""
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    logger = logging.getLogger("test_security_logger")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)

    sensitive_text = "Patient underwent confidential procedure PX0001 with severe symptoms"
    sensitive_prompt = "System instruction: extract claim details"

    # Attempt to log with forbidden fields
    log_event(
        logger,
        level=logging.INFO,
        claim_id="CLM12345678",
        node="extract",
        outcome="ok",
        raw_text=sensitive_text,
        prompt=sensitive_prompt,
        notes=sensitive_text,
    )

    output = stream.getvalue()
    record = json.loads(output.strip())

    # Allowed keys only
    assert sensitive_text not in output
    assert sensitive_prompt not in output
    assert "raw_text" not in record
    assert "prompt" not in record
    assert "notes" not in record
