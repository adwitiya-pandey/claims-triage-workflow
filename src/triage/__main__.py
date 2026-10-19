"""Entrypoint for running the claims triage API server per T14."""

import uvicorn

from triage.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "triage.api:app",
        host="127.0.0.1",
        port=8000,
        log_level=settings.LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
