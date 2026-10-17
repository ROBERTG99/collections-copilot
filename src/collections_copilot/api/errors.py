"""RFC 9457 problem details with correlation_id for every error response."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)
PROBLEM = "application/problem+json"

TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Content Too Large",
    415: "Unsupported Media Type",
    422: "Unprocessable Content",
    429: "Too Many Requests",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        detail: str | None = None,
        *,
        code: str | None = None,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail or TITLES.get(status, "Error"))
        self.status = status
        self.detail = detail
        self.code = code
        self.extra = extra or {}
        self.headers = headers or {}


def correlation_id(request: Request) -> str:
    cid = request.scope.get("cc.correlation_id")
    if not cid:
        cid = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        request.scope["cc.correlation_id"] = cid
    return str(cid)


def problem(
    request: Request,
    status: int,
    detail: str | None = None,
    code: str | None = None,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    cid = correlation_id(request)
    body: dict[str, Any] = {
        "type": f"https://collections-copilot.example/problems/{code or status}",
        "title": TITLES.get(status, "Error"),
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "correlation_id": cid,
    }
    if code:
        body["code"] = code
    body.update(extra or {})
    return JSONResponse(
        body,
        status_code=status,
        media_type=PROBLEM,
        headers={"X-Correlation-ID": cid, **(headers or {})},
    )


def install(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(request: Request, exc: ProblemError) -> JSONResponse:
        return problem(request, exc.status, exc.detail, exc.code, exc.extra, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem(
            request, exc.status_code, str(exc.detail), headers=getattr(exc, "headers", None)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": [str(p) for p in e.get("loc", [])], "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return problem(
            request, 422, "request validation failed", "validation_error", {"errors": errors}
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # TelemetryMiddleware already logged it with the correlation and trace ids.
        return problem(request, 500, "internal error", "internal_error")
