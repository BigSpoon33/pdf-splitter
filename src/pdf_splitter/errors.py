"""Every error the API returns: `{code, message}` plus, on a 500, `request_id`; on a 422, `errors`.

One table for all codes, so the SPA's message map (STORY-008) has a single source. Bodies never carry a path
or a job id.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

MESSAGES = {
    # POST /api/jobs (STORY-005)
    "too_large": "The file is larger than the upload limit.",
    "not_pdf": "The file is not a PDF.",
    "encrypted": "The PDF is password-protected.",
    "too_many_pages": "The PDF has more pages than the limit.",
    "no_text_layer": "This PDF has no text layer (it looks scanned). OCR isn't supported yet — run OCR on it first, then upload it again.",
    "unreadable": "The PDF could not be read.",
    # STORY-012: the limits (Architecture § janitor, § API Interface)
    "rate_limited": "Too many uploads from your network. Wait a while and try again.",
    "disk_full": "The server has no room for new uploads right now. Try again later.",
    # STORY-013 gate r1: the in-flight upload cap (upload.py `UploadGuard`)
    "overloaded": "The server is busy with other uploads right now. Try again in a moment.",
    # STORY-013 gate r2: a request body (a plan, never the upload — Caddy bounds that) that stopped arriving
    # (body_guard.py). Gate r3 took the upload watchdog out; the code stays for the body guard's 408.
    "too_slow": "The request stalled and was abandoned. Check your connection and try again.",
    # jobs
    "not_found": "There is no such job.",
    "expired": "This job was deleted (files are kept 24 hours).",
    "not_ready": "The job is not ready for this yet.",
    "busy": "The job is being processed; try again when it has finished.",
    "invalid": "The request is not valid.",
    # POST /sections/{i}/plan: `i` names no section of the list being planned (a 422, not the job-level 404)
    "no_section": "There is no section with that number in the list.",
    "preview_failed": "The preview could not be rendered.",
    "internal": "Something went wrong.",
}


class ApiError(Exception):
    """A rejection with a code from `MESSAGES`; the handler turns it into the JSON body."""

    def __init__(self, status: int, code: str, message: str | None = None, **extra: Any) -> None:
        super().__init__(code)
        self.status, self.code, self.extra = status, code, extra
        self.message = message or MESSAGES[code]


def error_response(status: int, code: str, message: str | None = None, **extra: Any) -> JSONResponse:
    return JSONResponse({"code": code, "message": message or MESSAGES[code], **extra}, status_code=status)


def field_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pydantic's error list reduced to what a client needs (`input` echoes the body, `ctx` can hold objects)."""
    return [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in errors]


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, **exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's own 422 (a bad path/query/body) in the same shape as the Plan's field errors.
        return error_response(422, "invalid", errors=field_errors(exc.errors()))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # The router's own 404/405 (no such route), so every error body has a `code`.
        if exc.status_code == 404:
            return error_response(404, "not_found", "There is no such resource.")
        return error_response(exc.status_code, "invalid", str(exc.detail))
