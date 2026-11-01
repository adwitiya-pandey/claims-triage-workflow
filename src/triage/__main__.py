"""CLI entry point for triage service."""

import uvicorn

from triage.api import create_app
from triage.config import TriageConfig
from triage.logging_setup import setup_logging


def main() -> None:
    """Run FastAPI application with uvicorn server."""
    config = TriageConfig.load()
    setup_logging(config.LOG_LEVEL)
    app = create_app(config=config)
    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT)


if __name__ == "__main__":
    main()
