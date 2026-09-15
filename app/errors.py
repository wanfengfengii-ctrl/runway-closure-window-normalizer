"""Stable error codes and the uniform error envelope.

Every client-facing error response has the shape::

    {
        "error": {
            "code": "<stable top-level code>",
            "message": "<human readable summary>",
            "details": [
                {"code": "<stable detail code>", "path": "<field path>", "message": "..."}
            ],
        }
    }

Field paths use dotted names with list indexes in brackets, e.g.
``closures[2].start``.  ``$`` denotes the request body as a whole.
"""

from __future__ import annotations

from typing import Any, Iterable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Stable detail codes (part of the public contract — never rename silently).
VALIDATION_ERROR = "VALIDATION_ERROR"
INVALID_JSON = "INVALID_JSON"
MISSING_FIELD = "MISSING_FIELD"
INVALID_TYPE = "INVALID_TYPE"
UNEXPECTED_FIELD = "UNEXPECTED_FIELD"
INVALID_TIME_FORMAT = "INVALID_TIME_FORMAT"
INVALID_RANGE = "INVALID_RANGE"
INVALID_VALUE = "INVALID_VALUE"
NOT_FOUND = "NOT_FOUND"
METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
HTTP_ERROR = "HTTP_ERROR"


def format_path(loc: Iterable[Any]) -> str:
    """Render a pydantic ``loc`` tuple as a field path string."""
    parts: list[str] = []
    for item in loc:
        if item == "body":
            continue
        if isinstance(item, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{item}]"
            else:
                parts.append(f"[{item}]")
        else:
            parts.append(str(item))
    return ".".join(parts) if parts else "$"


def make_detail(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def error_body(code: str, message: str, details: list[dict[str, str]]) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


class ApiValidationError(Exception):
    """Business-rule validation failure carrying a full list of details."""

    def __init__(self, details: list[dict[str, str]]) -> None:
        super().__init__("request validation failed")
        self.details = details


def _pydantic_detail_code(error_type: str) -> str:
    if error_type == "missing":
        return MISSING_FIELD
    if error_type == "extra_forbidden":
        return UNEXPECTED_FIELD
    if error_type.startswith("json"):
        return INVALID_JSON
    return INVALID_TYPE


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiValidationError)
    async def handle_api_validation(_: Request, exc: ApiValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(VALIDATION_ERROR, "Request validation failed.", exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = []
        for err in exc.errors():
            error_type = str(err.get("type", ""))
            path = format_path(err.get("loc", ()))
            code = _pydantic_detail_code(error_type)
            if code == INVALID_JSON:
                path = "$"
            details.append(make_detail(code, path, str(err.get("msg", "Invalid value."))))
        return JSONResponse(
            status_code=422,
            content=error_body(VALIDATION_ERROR, "Request validation failed.", details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: NOT_FOUND, 405: METHOD_NOT_ALLOWED}.get(exc.status_code, HTTP_ERROR)
        detail = make_detail(code, request.url.path, str(exc.detail))
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, str(exc.detail), [detail]),
        )
