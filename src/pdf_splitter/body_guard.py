"""`BodyGuard`: every request body except the upload's is small and read whole by its route, so it is read whole
HERE first, under a bound, before the route sees any of it.

Without this, ~`LIMIT_CONCURRENCY` clients each holding a `PUT /api/jobs/x/plan` open with a body that never
finishes (the job need not exist: the body is read before the route looks it up) sat inside uvicorn's connection
ceiling for as long as they liked and the api answered nobody (STORY-013 gate r2). Now a body must be declared
(`Content-Length`; a chunked body is 411), fit `MAX_JSON_BYTES` (413) and arrive whole within `BODY_TIMEOUT`
seconds (408) — or the connection is closed and the route never runs. Buffering it changes nothing about memory:
the route would have read the same bytes into memory itself.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import Settings
from .upload import UPLOAD_PATH, refuse

log = logging.getLogger(__name__)


class BodyGuard:
    def __init__(self, app: ASGIApp, settings: Settings, clock: Callable[[], float] = time.monotonic) -> None:
        self.app, self.settings, self.clock = app, settings, clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or (scope["method"] == "POST" and scope["path"] == UPLOAD_PATH):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        declared = headers.get("content-length")
        if declared is None:
            if "transfer-encoding" in headers:
                await refuse("invalid", status=411, message="The request body needs a Content-Length.")(scope, receive, send)
                return
            # No body at all (a GET, a bare POST /cut, a DELETE): nothing to read ahead of the route.
            await self.app(scope, receive, send)
            return
        if not declared.isdigit():
            await refuse("invalid")(scope, receive, send)
            return
        length = int(declared)
        if length > self.settings.max_json_bytes:
            await refuse("invalid", status=413, message="The request body is too large.")(scope, receive, send)
            return
        if length == 0:
            await self.app(scope, receive, send)
            return

        deadline = self.clock() + self.settings.body_timeout
        messages: list[Message] = []
        received = 0
        while True:
            try:
                message = await asyncio.wait_for(receive(), max(0.0, deadline - self.clock()))
            except TimeoutError:
                log.info("%s %s: body not complete after %.0fs", scope["method"], scope["path"], self.settings.body_timeout)
                await refuse("too_slow")(scope, receive, send)
                return
            if message["type"] == "http.disconnect":
                # The client left mid-body: there is nobody to answer and nothing for the route to read.
                return
            messages.append(message)
            received += len(message.get("body", b""))
            if received > self.settings.max_json_bytes:
                # The header was honest at the door; a server layer that does not enforce framing is not.
                await refuse("invalid", status=413, message="The request body is too large.")(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        replay = iter(messages)

        async def replayed() -> Message:
            try:
                return next(replay)
            except StopIteration:
                return await receive()

        await self.app(scope, replayed, send)
