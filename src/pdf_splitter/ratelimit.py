"""`RATE_PER_HOUR` uploads per client per sliding hour (PRD AC-11), keyed by a salted hash of the IP (ADR-007).

The salt is `sha256(secret, UTC date)`: the same for every api thread and process that shares the secret, a
different one every day, so a hash in `jobs.ip_hash` or `rate` links to nothing after that day and never to the
raw address. The window does not care about the date: in the first hour of a UTC day it also counts the hits
recorded under yesterday's hash, so the salt turning never empties anyone's window. Every function takes `now`,
so the tests never sleep.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Request

from .store import Store

WINDOW = timedelta(hours=1)
# Drawn once per process: the default when `PDFSPLIT_IP_SALT` is unset (see config.py for the multi-process caveat).
_PROCESS_SECRET = secrets.token_hex(16)


def utcnow() -> datetime:
    """The api's clock for the window; a test freezes it here."""
    return datetime.now(UTC)


def client_ip(request: Request, trusted_proxy: str | None) -> str:
    """The peer address, or — only when the peer IS the trusted proxy — the last hop in its `X-Forwarded-For`
    (the one the proxy itself appended; anything before it is client-supplied)."""
    peer = request.client.host if request.client else "unknown"
    if trusted_proxy and peer == trusted_proxy:
        hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
        if hops:
            return hops[-1]
    return peer


def ip_hash(ip: str, now: datetime | None = None, secret: str | None = None) -> str:
    day = (now or utcnow()).astimezone(UTC).date().isoformat()
    salt = hashlib.sha256(f"{secret or _PROCESS_SECRET}:{day}".encode()).digest()
    return hashlib.sha256(salt + ip.encode()).hexdigest()


def window_hashes(ip: str, now: datetime, secret: str | None = None) -> list[str]:
    """Today's hash first (new hits are recorded under it), then yesterday's while the sliding hour still
    reaches back into yesterday: the hits a client made before midnight keep counting until they are an hour old."""
    hashes = [ip_hash(ip, now, secret)]
    if (now - WINDOW).astimezone(UTC).date() != now.astimezone(UTC).date():
        hashes.append(ip_hash(ip, now - WINDOW, secret))
    return hashes


def take_slot(
    store: Store, ip: str, per_hour: int, *, now: datetime | None = None, secret: str | None = None
) -> tuple[str, int | None]:
    """Claim one of the client's `per_hour` slots in the sliding hour, atomically (`Store.take_rate_slot`).
    Returns today's hash of the client (the `jobs.ip_hash` value) and None, or the whole seconds until a slot
    frees up when none was."""
    now = now or utcnow()
    hashes = window_hashes(ip, now, secret)
    return hashes[0], store.take_rate_slot(hashes, since=now - WINDOW, limit=per_hour, now=now)
