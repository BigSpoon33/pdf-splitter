"""`RATE_PER_HOUR` uploads per client per sliding hour (PRD AC-11), keyed by a salted hash of the IP (ADR-007).

The salt is `sha256(secret, UTC date)`: the same for every api thread and process that shares the secret, a
different one every day, so a hash in `jobs.ip_hash` or `rate` links to nothing after that day and never to the
raw address. The window does not care about the date: in the first hour of a UTC day it also counts the hits
recorded under yesterday's hash, so the salt turning never empties anyone's window. The clock is read by the
store while it holds the write lock (`utcnow`, the one seam a test freezes — nothing here sleeps): the hashes
are dated, so a reading taken before the lock could count under the wrong day.
"""

from __future__ import annotations

import hashlib
import ipaddress
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import Request

from .store import Store, utcnow

WINDOW = timedelta(hours=1)
# Drawn once per process: the default when `PDFSPLIT_IP_SALT` is unset (see config.py for the multi-process caveat).
_PROCESS_SECRET = secrets.token_hex(16)


def canonical(address: str) -> str:
    """One spelling per address: an IPv6 peer arrives however the socket prints it (`fd00::10`), the setting
    however the operator typed it (`fd00:0:0:0::10`). Anything that is not an IP is compared as written."""
    try:
        return str(ipaddress.ip_address(address))
    except ValueError:
        return address


def trusted_proxies(setting: str | None) -> frozenset[str]:
    """`PDFSPLIT_TRUSTED_PROXY`, comma-separated: the proxy has one address per network family and may connect
    over either."""
    return frozenset(canonical(p.strip()) for p in (setting or "").split(",") if p.strip())


def client_ip(request: Request, trusted_proxy: str | None) -> str:
    """The peer address, or — only when the peer IS one of the trusted proxies — the last hop in its
    `X-Forwarded-For` (the one the proxy itself appended; anything before it is client-supplied)."""
    peer = request.client.host if request.client else "unknown"
    if trusted_proxy and canonical(peer) in trusted_proxies(trusted_proxy):
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
    store: Store,
    ip: str,
    per_hour: int,
    *,
    secret: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[str, int | None]:
    """Claim one of the client's `per_hour` slots in the sliding hour, atomically (`Store.take_rate_slot`): the
    store reads `clock` under its lock and the window (today's hash, and yesterday's while the hour reaches back
    into it) is built from that reading. Returns today's hash of the client (the `jobs.ip_hash` value) and None,
    or the whole seconds until a slot frees up when none was."""

    def window(now: datetime) -> tuple[list[str], datetime]:
        return window_hashes(ip, now, secret), now - WINDOW

    # Looked up at call time, so a frozen `ratelimit.utcnow` reaches the store.
    return store.take_rate_slot(window, limit=per_hour, clock=clock or utcnow)
