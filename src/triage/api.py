"""FastAPI endpoints for claims triage workflow per SPEC 14."""

import re
import secrets
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi import Path as FastPath
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StringConstraints

from triage.config import get_settings
from triage.models import ReviewAction
from triage.service import ConflictError, NotFoundError, TriageService


class TriageRequest(BaseModel):
    """Request body for initiating claims triage."""

    model_config = ConfigDict(extra="forbid")

    claim_id: Annotated[
        str,
        StringConstraints(pattern=r"^CLM[0-9]{8}$"),
    ]
    reprocess: bool = False


def create_app(service: TriageService | None = None, api_key: str | None = None) -> FastAPI:
    """Create and configure FastAPI application instance."""
    app = FastAPI(title="Claims Triage Workflow API", docs_url=None, redoc_url=None)
    settings = get_settings()

    active_service = service if service is not None else TriageService()
    expected_key = api_key if api_key is not None else settings.TRIAGE_API_KEY

    # API key authentication dependency
    def require_api_key(
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> str:
        if not x_api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing X-API-Key header",
            )
        if not secrets.compare_digest(x_api_key, expected_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )
        return x_api_key

    # Custom validation error handler ensuring no internal state leaks
    @app.exception_handler(RequestValidationError)
    def validation_exception_handler(request: Any, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Validation error: invalid request payload or parameters"},
        )

    # Global unhandled exception handler to prevent traceback and claim text leakage (SPEC 14)
    @app.exception_handler(Exception)
    def global_exception_handler(request: Any, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    @app.get("/health", status_code=status.HTTP_200_OK)
    def health_check() -> dict[str, str]:
        """Liveness check confirming config loaded and service ready."""
        # Confirm database path is accessible / parent directory writable
        db_path = active_service.db_path
        if not db_path.parent.exists():
            db_path.parent.mkdir(parents=True, exist_ok=True)
        return {"status": "ok", "service": "claims-triage-workflow"}

    @app.post("/triage", status_code=status.HTTP_200_OK)
    def start_triage(
        req: TriageRequest,
        _: Annotated[str, Depends(require_api_key)],
    ) -> dict[str, Any]:
        """Start or retrieve triage assessment for a claim."""
        try:
            return active_service.start_triage(claim_id=req.claim_id, reprocess=req.reprocess)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

    @app.get("/reviews/pending", status_code=status.HTTP_200_OK)
    def list_pending_reviews(
        _: Annotated[str, Depends(require_api_key)],
    ) -> list[dict[str, Any]]:
        """List all currently awaiting human reviews."""
        return active_service.list_pending()

    @app.post("/reviews/{thread_id}", status_code=status.HTTP_200_OK)
    def submit_review(
        thread_id: Annotated[str, FastPath(...)],
        action: ReviewAction,
        _: Annotated[str, Depends(require_api_key)],
    ) -> dict[str, Any]:
        """Submit a human review action to resume a paused claim triage workflow."""
        try:
            return active_service.submit_review(thread_id, action)
        except NotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    @app.get("/decisions/{claim_id}", status_code=status.HTTP_200_OK)
    def get_decisions(
        claim_id: Annotated[str, FastPath(...)],
        _: Annotated[str, Depends(require_api_key)],
    ) -> list[dict[str, Any]]:
        """Retrieve all historical triage decisions for a claim, ordered newest first."""
        if not re.fullmatch(r"CLM[0-9]{8}", claim_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid claim ID format",
            )
        try:
            return active_service.get_decisions(claim_id)
        except NotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc

    return app


app = create_app()
