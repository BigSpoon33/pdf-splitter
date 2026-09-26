"""`BodyGuard`: every request body except the upload's is small and read whole by its route, so it is read whole
HERE first, under a bound, before the route sees any of it.

Without this, ~`LIMIT_CONCURRENCY` clients each holding a `PUT /api/jobs/x/plan` open with a body that never
finishes (the job need not exist: the body is read before the route looks it up) sat inside uvicorn's connection
ceiling for as long as they liked and the api answered nobody (STORY-013 gate r2). Now a body must be declared
(`Content-Length`; a chunked body is 411), fit `MAX_JSON_BYTES` (413) and arrive whole within `BODY_TIMEOUT`
seconds (408) — or the connection is closed and the route never runs. One client may have at most
`MAX_BODIES_PER_CLIENT` bodies in flight (429, unread): re-opening held bodies as they time out could otherwise
keep the ceiling full from one address (gate r3). Buffering a body changes nothing about memory: the route would
have read the same bytes into memory itself.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from fastapi import Request
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import ratelimit
from .config import Settings
from .upload import UPLOAD_PATH, refuse

log = logging.getLogger(__name__)

# nginx's code for a client that closed the connection before the server answered. A body that stops with a
# disconnect has nobody to answer, but the ASGI contract (and the access log's `call_next`) needs a response
# where a request was, or the missing one is logged as a 500 with a traceback (gate r3). The server drops it:
# uvicorn's `send` is a no-op once the client is gone, so nothing goes on the wire.
CLIENT_CLOSED = 499
# A held body frees a client's cap within BODY_TIMEOUT at the latest; most are done in milliseconds.
RETRY_AFTER_BODY = "5"


class BodyGuard:
    def __init__(self, app: ASGIApp, settings: Settings, clock: Callable[[], float] = time.monotonic) -> None:
        self.app, self.settings, self.clock = app, settings, clock
        self.per_client: dict[str, int] = {}

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

        # The undated key the upload cap uses too (ratelimit.client_key): a cap charged before midnight must be
        # released under the same key after it. Charged for as long as the body is being read, not for the route.
        client = ratelimit.client_key(
            ratelimit.client_ip(Request(scope), self.settings.trusted_proxy), self.settings.ip_salt
        )
        if self.per_client.get(client, 0) >= self.settings.max_bodies_per_client:
            log.info("%s %s refused: %s already has %d bodies in flight",
                     scope["method"], scope["path"], client[:8], self.settings.max_bodies_per_client)
            await refuse("rate_limited", {"Retry-After": RETRY_AFTER_BODY})(scope, receive, send)
            return
        self.per_client[client] = self.per_client.get(client, 0) + 1
        try:
            messages = await self.read_whole(scope, receive, send)
        finally:
            if self.per_client[client] == 1:
                del self.per_client[client]
            else:
                self.per_client[client] -= 1
        if messages is None:
            return

        replay = iter(messages)

        async def replayed() -> Message:
            try:
                return next(replay)
            except StopIteration:
                return await receive()

        await self.app(scope, replayed, send)

    async def read_whole(self, scope: Scope, receive: Receive, send: Send) -> list[Message] | None:
        """The body's messages, or None once this has answered (a timeout, an oversize, a disconnect)."""
        deadline = self.clock() + self.settings.body_timeout
        messages: list[Message] = []
        received = 0
        while True:
            try:
                message = await asyncio.wait_for(receive(), max(0.0, deadline - self.clock()))
            except TimeoutError:
                log.info("%s %s: body not complete after %.0fs", scope["method"], scope["path"], self.settings.body_timeout)
                await refuse("too_slow")(scope, receive, send)
                return None
            if message["type"] == "http.disconnect":
                await client_closed(scope, receive, send)
                return None
            messages.append(message)
            received += len(message.get("body", b""))
            if received > self.settings.max_json_bytes:
                # The header was honest at the door; a server layer that does not enforce framing is not.
                await refuse("invalid", status=413, message="The request body is too large.")(scope, receive, send)
                return None
            if not message.get("more_body", False):
                return messages


async def client_closed(scope: Scope, receive: Receive, send: Send) -> None:
    """The empty `CLIENT_CLOSED` response: a request the client abandoned mid-body is logged as such, never as
    a failure of ours."""
    await send({
        "type": "http.response.start",
        "status": CLIENT_CLOSED,
        "headers": [(b"content-length", b"0"), (b"connection", b"close")],
    })
    await send({"type": "http.response.body", "body": b""})
