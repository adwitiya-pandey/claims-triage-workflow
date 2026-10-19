"""Structured JSON logging configuration per SPEC 16 (S7)."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# Disallowed fields that must never appear in logs (S7)
FORBIDDEN_KEYS = {
    "raw_text",
    "clean_text",
    "claim_text",
    "prompt",
    "notes",
    "model_output",
    "response_text",
    "text",
    "submission",
}

ALLOWED_EXTRA_KEYS = {
    "claim_id",
    "thread_id",
    "node",
    "duration_ms",
    "outcome",
    "rule_ids",
    "extra_info",
}


class JSONFormatter(logging.Formatter):
    """Format log records as single-line JSON, omitting sensitive text."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }

        # Safely copy allowed metadata
        for key in ALLOWED_EXTRA_KEYS:
            if hasattr(record, key):
                val = getattr(record, key)
                if val is not None:
                    log_obj[key] = val

        return json.dumps(log_obj, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger with the JSONFormatter."""
    root_logger = logging.getLogger()
    root_logger.setLevel(level.upper())

    # Avoid duplicate handlers
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Get a named logger."""
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    level: int = logging.INFO,
    claim_id: str | None = None,
    thread_id: str | None = None,
    node: str | None = None,
    duration_ms: float | None = None,
    outcome: str | None = None,
    rule_ids: list[str] | None = None,
    message: str = "",
    **kwargs: Any,
) -> None:
    """Log a structured event while strictly filtering forbidden text fields."""
    extra: dict[str, Any] = {}
    if claim_id is not None:
        extra["claim_id"] = claim_id
    if thread_id is not None:
        extra["thread_id"] = thread_id
    if node is not None:
        extra["node"] = node
    if duration_ms is not None:
        extra["duration_ms"] = round(duration_ms, 2)
    if outcome is not None:
        extra["outcome"] = outcome
    if rule_ids is not None:
        extra["rule_ids"] = rule_ids

    # Allow harmless extra_info string if not in forbidden keys
    for k, v in kwargs.items():
        if k not in FORBIDDEN_KEYS and k in ALLOWED_EXTRA_KEYS:
            extra[k] = v

    logger.log(level, message, extra=extra)
