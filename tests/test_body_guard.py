"""STORY-013 gate r2: `BodyGuard` — every body but the upload's is declared, small, and read whole within a bound
before the route runs, so a held-open PUT can't sit inside uvicorn's connection ceiling for as long as it likes."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import pytest
from test_upload import assert_unread, reading_client

from pdf_splitter import body_guard
from pdf_splitter.app import create_app
from pdf_splitter.config import Settings
from pdf_splitter.errors import MESSAGES

PLAN_URL = "/api/jobs/no-such-job/plan"


def test_a_chunked_or_oversized_body_is_refused_before_the_route_reads_it(settings: Settings) -> None:
    client, reading = reading_client(settings)
    with client:
        # httpx sends an iterator body chunked, without Content-Length.
        r = client.put(PLAN_URL, content=iter([b"{", b"}"]), headers={"Content-Type": "application/json"})
        assert (r.status_code, r.json()["code"]) == (411, "invalid"), r.text
        assert r.headers["connection"] == "close"
        r = client.put(PLAN_URL, content=b"0" * (4 * 1024 * 1024 + 1), headers={"Content-Type": "application/json"})
        assert (r.status_code, r.json()) == (413, {"code": "invalid", "message": "The request body is too large."})
        assert r.headers["connection"] == "close"
        assert_unread(reading)
        # Requests without a body are none of the guard's business; a small body reaches the route whole
        # (the 404 is the route's: it looked the job up, which means it had the body).
        assert client.get("/api/health").status_code == 200
        assert client.delete("/api/jobs/no-such-job").status_code == 404
        assert client.post("/api/jobs/no-such-job/cut").status_code == 404
        body = json.dumps({"source": "headings", "sections": []}).encode()
        r = client.put(PLAN_URL, content=body, headers={"Content-Type": "application/json"})
        assert (r.status_code, r.json()["code"]) == (404, "not_found")
        assert (reading.messages, reading.bytes) == (1, len(body))


# --- the bound, on an ASGI harness with an injected clock -----------------------------------------


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class Echo:
    """The route's stand-in: reads the whole body and answers 200 with it."""

    def __init__(self) -> None:
        self.body, self.ran = b"", False

    async def __call__(self, scope, receive, send) -> None:
        self.ran = True
        while True:
            message = await receive()
            self.body += message.get("body", b"")
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": self.body})


def put_scope(length: int | None, extra: list[tuple[bytes, bytes]] | None = None) -> dict:
    headers = [(b"content-type", b"application/json")] + (extra or [])
    if length is not None:
        headers.append((b"content-length", str(length).encode()))
    return {
        "type": "http", "http_version": "1.1", "method": "PUT", "path": PLAN_URL, "scheme": "http",
        "query_string": b"", "server": ("test", 80), "client": ("203.0.113.9", 1234), "headers": headers,
    }


def playing(clock: Clock, steps: list[tuple[float, dict | None]]):
    """A `receive` that advances the clock by `dt` and hands over the message; past a `None` it blocks for good."""
    it, silent = iter(steps), False

    async def receive():
        nonlocal silent
        if not silent:
            dt, message = next(it)
            clock.t += dt
            if message is not None:
                return message
            silent = True
        await asyncio.Event().wait()

    return receive


def harness(tmp_path: Path, clock, **overrides):
    echo, sent = Echo(), []

    async def send(message):
        sent.append(message)

    return body_guard.BodyGuard(echo, Settings(jobs_dir=tmp_path / "jobs", **overrides), clock=clock), echo, sent, send


def statuses(sent: list) -> list[int]:
    return [m["status"] for m in sent if m["type"] == "http.response.start"]


def chunk(body: bytes, more: bool = True) -> dict:
    return {"type": "http.request", "body": body, "more_body": more}


def test_the_route_gets_the_whole_body_replayed_in_order(tmp_path: Path) -> None:
    clock = Clock()
    guard, echo, sent, send = harness(tmp_path, clock)
    receive = playing(clock, [(0.0, chunk(b'{"a":')), (5.0, chunk(b"1}", more=False))])
    asyncio.run(guard(put_scope(7), receive, send))
    assert echo.body == b'{"a":1}'
    assert statuses(sent) == [200] and sent[1]["body"] == b'{"a":1}'


def test_a_body_that_has_not_arrived_whole_by_the_bound_is_408(tmp_path: Path) -> None:
    """Ten bytes, then a chunk that lands after the 20 s bound: the next wait has no time left and gives up —
    408 `too_slow`, Connection: close, the route never ran."""
    clock = Clock()
    guard, echo, sent, send = harness(tmp_path, clock)
    receive = playing(clock, [(0.0, chunk(b"0" * 10)), (21.0, chunk(b"0" * 10)), (0.0, None)])
    asyncio.run(guard(put_scope(100), receive, send))
    assert not echo.ran
    assert statuses(sent) == [408]
    headers = {k.decode(): v.decode() for k, v in sent[0]["headers"]}
    assert headers["connection"] == "close"
    assert json.loads(sent[1]["body"]) == {"code": "too_slow", "message": MESSAGES["too_slow"]}


def test_a_silent_client_is_408_when_the_wait_itself_times_out(tmp_path: Path) -> None:
    """The real-clock path: a 50 ms bound and a client that sends nothing after the headers."""
    guard, echo, sent, send = harness(tmp_path, time.monotonic, body_timeout=0.05)
    asyncio.run(guard(put_scope(100), playing(Clock(), [(0.0, None)]), send))
    assert not echo.ran and statuses(sent) == [408]


def test_a_client_that_leaves_mid_body_gets_an_empty_499_and_no_route(tmp_path: Path) -> None:
    """Gate r3 (round 3 finding 6): answering nothing left the access log's `call_next` without a response —
    a 500 and a traceback for every client that gave up mid-body. The guard now answers nginx's 499 with no
    body (uvicorn drops it on the dead connection) and the cap is released."""
    clock = Clock()
    guard, echo, sent, send = harness(tmp_path, clock)
    receive = playing(clock, [(0.0, chunk(b"0" * 10)), (1.0, {"type": "http.disconnect"})])
    asyncio.run(guard(put_scope(100), receive, send))
    assert not echo.ran
    assert statuses(sent) == [body_guard.CLIENT_CLOSED] == [499]
    assert sent[1]["body"] == b"" and guard.per_client == {}


def test_a_mid_body_disconnect_is_logged_as_499_not_as_a_500(settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    """The same disconnect through the whole app (access log around the guard): the access line says 499 and
    nothing is logged at ERROR — no "No response returned." traceback."""
    caplog.set_level(logging.INFO)
    app, clock, sent = create_app(settings), Clock(), []
    receive = playing(clock, [(0.0, chunk(b'{"a":')), (1.0, {"type": "http.disconnect"})])

    async def send(message):
        sent.append(message)

    asyncio.run(app(put_scope(100), receive, send))
    assert statuses(sent) == [499]
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert not any("Traceback" in r.getMessage() for r in caplog.records)
    assert any(r.getMessage().startswith("PUT /api/jobs/") and " 499 " in r.getMessage()
               for r in caplog.records if r.name == "pdf_splitter.access")


def test_one_client_holds_at_most_max_bodies_per_client_and_others_get_through(tmp_path: Path) -> None:
    """Gate r3 (round 3 finding 2): the 20 s bound freed a held body, but a client re-opening `LIMIT_CONCURRENCY`
    of them kept the ceiling full. Now a client's ninth body in flight is 429 at once — unread, `Retry-After`,
    `Connection: close` — while another client's body is read and routed; the cap frees as bodies end."""
    guard, echo, _sent, send = harness(tmp_path, Clock(), max_bodies_per_client=8)

    async def scenario() -> None:
        never = playing(Clock(), [(0.0, None)])
        held = [asyncio.create_task(guard(put_scope(100), never, send)) for _ in range(8)]
        for _ in range(3):
            await asyncio.sleep(0)
        assert guard.per_client and next(iter(guard.per_client.values())) == 8
        ninth_sent, ninth_read = [], []

        async def ninth_receive():
            ninth_read.append(1)
            return chunk(b"0" * 100, more=False)

        async def ninth_send(message):
            ninth_sent.append(message)

        await guard(put_scope(100), ninth_receive, ninth_send)
        assert statuses(ninth_sent) == [429] and ninth_read == []
        headers = {k.decode(): v.decode() for k, v in ninth_sent[0]["headers"]}
        assert headers["retry-after"] == body_guard.RETRY_AFTER_BODY and headers["connection"] == "close"
        assert json.loads(ninth_sent[1]["body"]) == {"code": "rate_limited", "message": MESSAGES["rate_limited"]}
        # Another client is not behind the first one's cap.
        other = {**put_scope(7), "client": ("198.51.100.4", 1234)}
        other_sent = []

        async def other_send(message):
            other_sent.append(message)

        await guard(other, playing(Clock(), [(0.0, chunk(b'{"a":1}', more=False))]), other_send)
        assert echo.ran and statuses(other_sent) == [200] and other_sent[1]["body"] == b'{"a":1}'
        for task in held:
            task.cancel()
        await asyncio.gather(*held, return_exceptions=True)
        assert guard.per_client == {}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("scope", "status", "message"),
    [
        (put_scope(None, [(b"transfer-encoding", b"chunked")]), 411, "The request body needs a Content-Length."),
        (put_scope(4 * 1024 * 1024 + 1), 413, "The request body is too large."),
        (put_scope(None, [(b"content-length", b"lots")]), 400, MESSAGES["invalid"]),
    ],
    ids=["chunked", "oversized", "malformed"],
)
def test_the_headers_alone_refuse_what_they_can(tmp_path: Path, scope: dict, status: int, message: str) -> None:
    guard, echo, sent, send = harness(tmp_path, Clock())
    asyncio.run(guard(scope, playing(Clock(), [(0.0, None)]), send))
    assert not echo.ran
    assert statuses(sent) == [status]
    assert json.loads(sent[1]["body"]) == {"code": "invalid", "message": message}


def test_a_dishonest_content_length_is_still_capped_by_what_arrives(tmp_path: Path) -> None:
    """The header passed the door; a server layer that does not enforce framing must not let more through."""
    guard, echo, sent, send = harness(tmp_path, Clock(), max_json_bytes=100)
    receive = playing(Clock(), [(0.0, chunk(b"0" * 60)), (0.0, chunk(b"0" * 60)), (0.0, None)])
    asyncio.run(guard(put_scope(100), receive, send))
    assert not echo.ran and statuses(sent) == [413]


def test_the_upload_is_left_to_the_upload_guard(tmp_path: Path) -> None:
    guard, echo, sent, send = harness(tmp_path, Clock())
    scope = {**put_scope(None, [(b"transfer-encoding", b"chunked")]), "method": "POST", "path": "/api/jobs"}
    asyncio.run(guard(scope, playing(Clock(), [(0.0, chunk(b"x", more=False))]), send))
    assert echo.ran and statuses(sent) == [200]
