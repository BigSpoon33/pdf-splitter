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

# Two rules, both needed (gate r2). A `new_job_id()` token is 22 chars of `[A-Za-z0-9_-]`, but an id glued
# to another id-alphabet char (`<id>x`, `x<id>`, `<id><id>`, `<id>-extra`, `<id>A` from `%41`) or truncated
# is still the credential, so rule 1 hashes every maximal run of the alphabet that is 16+ chars long, whole,
# wherever it sits. Greedy `{16,}` can only ever match a whole run, never a slice of one. Rule 2 hashes
# whatever follows `/api/jobs/` (any case, any number of slashes) as one segment regardless of length, so
# even a fragment too short for rule 1 never reaches the log from the slot that carries ids.
_ID_RUN = re.compile(r"[A-Za-z0-9_-]{16,}")
_JOBS_SLOT = re.compile(r"(/api/+jobs/+)([^/]+)", re.IGNORECASE)
# RFC 3986 path characters; everything else (controls, `%`, non-ASCII separators) is percent-encoded.
_PATH_SAFE = "/:@!$&'()*+,;=-._~"


def redact_path(path: str) -> str:
    # The slot rule runs first: a hash is 8 hex chars, so rule 1 leaves it alone and a real id in the slot
    # logs as `log_id(id)` either way, never as a hash of a hash.
    path = _JOBS_SLOT.sub(lambda m: m.group(1) + log_id(m.group(2)), path)
    return _ID_RUN.sub(lambda m: log_id(m.group(0)), path)


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
