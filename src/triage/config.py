"""Configuration management and validation per SPEC 15."""

import os
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, model_validator

# Automatically load local .env if present
load_dotenv()

SECRET_FIELDS = {"GEMINI_API_KEY", "CLAIMS_MCP_KEY", "TRIAGE_API_KEY"}


class Settings(BaseModel):
    """Application configuration with strict validation and secret masking."""

    model_config = ConfigDict(extra="ignore")

    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-3.8-flash"
    LLM_MODE: Literal["live", "record", "replay", "oracle"] = "replay"
    CASSETTE_PATH: str = "data/cassettes/cassette.jsonl"

    CLAIMS_TOOLS_BACKEND: Literal["fixture", "mcp"] = "fixture"
    CLAIMS_MCP_URL: str | None = None
    CLAIMS_MCP_KEY: str | None = None
    CLAIMS_MCP_STDIO_CMD: str | None = None

    HIGH_AMOUNT_THRESHOLD: float = 10000.0
    TIMELY_FILING_DAYS: int = 90
    MIN_EXTRACT_CONFIDENCE: float = 0.7
    TOP_K: int = 4

    TRIAGE_DB_PATH: str = "data/triage.db"
    CHECKPOINT_DB_PATH: str = "data/checkpoint.db"

    TRIAGE_API_KEY: str | None = None
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8800
    LOG_LEVEL: str = "INFO"

    @model_validator(mode="after")
    def validate_rules(self) -> "Settings":
        """Validate inter-field dependencies without echoing secrets."""
        if self.LLM_MODE in ("live", "record") and (
            not self.GEMINI_API_KEY or not self.GEMINI_API_KEY.strip()
        ):
            raise ValueError("GEMINI_API_KEY is required when LLM_MODE is live or record")

        if self.CLAIMS_TOOLS_BACKEND == "mcp":
            has_http = bool(self.CLAIMS_MCP_URL and self.CLAIMS_MCP_KEY)
            has_stdio = bool(self.CLAIMS_MCP_STDIO_CMD)
            if not (has_http or has_stdio):
                raise ValueError(
                    "MCP backend requires either CLAIMS_MCP_URL and CLAIMS_MCP_KEY, or CLAIMS_MCP_STDIO_CMD"
                )

        return self

    def __repr__(self) -> str:
        """Mask secret values in representation."""
        fields = []
        for k, v in self.__dict__.items():
            if k in SECRET_FIELDS and v is not None:
                fields.append(f"{k}='***'")
            else:
                fields.append(f"{k}={v!r}")
        return f"Settings({', '.join(fields)})"

    def __str__(self) -> str:
        return repr(self)


_settings_instance: Settings | None = None


def get_settings() -> Settings:
    """Return cached Settings instance loaded from environment."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings(**os.environ)
    return _settings_instance
