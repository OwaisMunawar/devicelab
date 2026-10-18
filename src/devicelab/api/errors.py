from __future__ import annotations

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from devicelab.domain.errors import (
    ConflictError,
    DomainError,
    ForbiddenError,
    NotFoundError,
    SettingNotSupportedError,
    UnauthorizedError,
    ValidationFailedError,
)

log = structlog.get_logger()

_STATUS: tuple[tuple[type[DomainError], int], ...] = (
    (NotFoundError, 404),
    (ConflictError, 409),
    (ValidationFailedError, 422),
    (UnauthorizedError, 401),
    (ForbiddenError, 403),
    (SettingNotSupportedError, 422),
)


def status_for(exc: DomainError) -> int:
    return next((code for kind, code in _STATUS if isinstance(exc, kind)), 400)


def envelope(
    request: Request, code: str, message: str, details: dict[str, object] | None = None
) -> dict[str, object]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "correlation_id": getattr(request.state, "correlation_id", None),
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, UnauthorizedError) else None
        return JSONResponse(
            envelope(request, exc.code, exc.message, exc.details),
            status_code=status_for(exc),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            {"path": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()
        ]
        return JSONResponse(
            envelope(
                request, "request_invalid", "request failed validation", {"problems": problems}
            ),
            status_code=422,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            envelope(request, f"http_{exc.status_code}", str(exc.detail)),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path)
        return JSONResponse(
            envelope(request, "internal_error", "internal server error"), status_code=500
        )
