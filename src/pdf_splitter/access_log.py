"""Request logging that never prints a job id (ADR-007: the id is the only credential)."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from .store import log_id

log = logging.getLogger("pdf_splitter.access")

_JOB_PATH = re.compile(r"^(/api/jobs/)([^/]+)")


def redact_path(path: str) -> str:
    return _JOB_PATH.sub(lambda m: m.group(1) + log_id(m.group(2)), path)


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
            redact_path(request.url.path),
            status,
            (time.perf_counter() - start) * 1000,
        )
