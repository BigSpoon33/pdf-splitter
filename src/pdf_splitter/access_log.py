"""Request logging that never prints a job id (ADR-007: the id is the only credential)."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable
from urllib.parse import quote

from fastapi import Request, Response

from .store import log_id

log = logging.getLogger("pdf_splitter.access")

# Anything shaped like a `new_job_id()` token (token_urlsafe(16): 22 url-safe chars) is hashed wherever
# it sits in the path. Matching the token rather than the `/api/jobs/` prefix means no spelling of the
# path (doubled slashes, dot segments, case, absolute form, `<id>.ext`) can smuggle a raw id into a log.
_JOB_ID = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{22}(?![A-Za-z0-9_-])")
# RFC 3986 path characters; everything else (controls, `%`, non-ASCII separators) is percent-encoded.
_PATH_SAFE = "/:@!$&'()*+,;=-._~"


def redact_path(path: str) -> str:
    return _JOB_ID.sub(lambda m: log_id(m.group(0)), path)


def loggable_path(path: str) -> str:
    """The redacted path, re-escaped: the ASGI path is percent-DECODED client input, and a raw ESC or
    U+2028 in a log line is a terminal/log-viewer injection."""
    return quote(redact_path(path), safe=_PATH_SAFE)


async def access_log(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        # The query string is dropped too: it is free-form client input we never need in logs.
        log.info(
            "%s %s %d %.1fms",
            request.method,
            loggable_path(request.scope.get("path", "")),
            status,
            (time.perf_counter() - start) * 1000,
        )
