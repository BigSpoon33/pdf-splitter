"""`RATE_PER_HOUR` uploads per client per sliding hour (PRD AC-11), keyed by a salted hash of the IP (ADR-007).

The salt is `sha256(secret, UTC date)`: the same for every api thread and process that shares the secret, a
different one every day, so a hash in `jobs.ip_hash` or `rate` links to nothing after that day and never to the
raw address. Every function takes `now`, so the tests never sleep.
"""

from __future__ import annotations

import hashlib
import math
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Request

from .store import Store

WINDOW = timedelta(hours=1)
# Drawn once per process: the default when `PDFSPLIT_IP_SALT` is unset (see config.py for the multi-process caveat).
_PROCESS_SECRET = secrets.token_hex(16)


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
    day = (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()
    salt = hashlib.sha256(f"{secret or _PROCESS_SECRET}:{day}".encode()).digest()
    return hashlib.sha256(salt + ip.encode()).hexdigest()


def retry_after(store: Store, ip_hash: str, per_hour: int, now: datetime | None = None) -> int | None:
    """None when another upload may go now; else the whole seconds until the oldest hit leaves the window."""
    now = now or datetime.now(UTC)
    hits = store.rate_hits(ip_hash, since=now - WINDOW)
    if len(hits) < per_hour:
        return None
    oldest = datetime.fromisoformat(hits[len(hits) - per_hour])
    return max(1, math.ceil((oldest + WINDOW - now).total_seconds()))


def record(store: Store, ip_hash: str, now: datetime | None = None) -> None:
    store.add_rate(ip_hash, now)
