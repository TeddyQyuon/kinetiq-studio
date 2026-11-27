from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ConflictError(AppError):
    status_code = 409
    code = "revision_conflict"


class ValidationFailedError(AppError):
    status_code = 422
    code = "validation_failed"


class QuotaConfirmationRequiredError(AppError):
    status_code = 409
    code = "quota_confirmation_required"


def register_error_handlers(app: FastAPI) -> None:
    from app.identity.access import require_access

    # Install the API-wide dependency before the feature routers are included.
    app.router.dependencies.append(Depends(require_access))

    @app.exception_handler(AppError)
    async def _handle_app_error(_req: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": exc.message, "details": exc.details},
        )
