"""STORY-012: the limits — the disk guard (AC-2), the rate window (AC-1), the janitor (AC-3 + the STORY-007
addendum) — every clock frozen (AC-5), so nothing here sleeps."""

from __future__ import annotations

import logging
import os
import shutil
import threading
from collections import namedtuple
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from helpers import DASH_ID, OTHER_ID, assert_id_gone, seed_job
from test_upload import assert_rejected, job_count, job_dirs, pdf_bytes, post
from test_worker import serve_in_thread, wait_for

from pdf_splitter import janitor, ratelimit, upload
from pdf_splitter.app import create_app
from pdf_splitter.config import Settings
from pdf_splitter.store import Store
from pdf_splitter.worker import runner as runner_mod
from pdf_splitter.worker.runner import Runner

T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
Usage = namedtuple("Usage", "total used free")
GB = 10**9


def make(store: Store, job_id: str, *, now: datetime = T0, state: str = "queued", kind: str = "analyze") -> dict:
    return store.create_job(job_id=job_id, ip_hash="h", filename="a.pdf", bytes=1, pages=1, ttl_hours=24,
                            now=now, state=state, kind=kind)


def request_from(peer: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return Request({"type": "http", "client": (peer, 1234), "headers": headers, "method": "POST", "path": "/"})


# ── AC-2: the disk guard ──────────────────────────────────────────────────────────────────────────────


def test_upload_refused_with_503_when_the_volume_is_nearly_full(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Path] = []

    def usage(path):
        seen.append(Path(path))
        return Usage(100 * GB, 99 * GB, int(1.9 * GB))

    monkeypatch.setattr(upload.shutil, "disk_usage", usage)
    with TestClient(create_app(settings)) as client:
        r = post(client, pdf_bytes())
    assert_rejected(r, settings, 503, "disk_full")           # nothing on disk, no row
    assert seen == [settings.jobs_dir]
    assert r.headers["x-request-id"]


def test_the_guard_is_min_free_gb_in_decimal_gb_like_health(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(jobs_dir=tmp_path / "jobs", min_free_gb=0.5)
    free = {"n": int(0.5 * GB)}
    monkeypatch.setattr(upload.shutil, "disk_usage", lambda path: Usage(GB, GB - free["n"], free["n"]))
    with TestClient(create_app(settings)) as client:
        assert post(client, pdf_bytes()).status_code == 201      # exactly the threshold is not "less than"
        assert client.get("/api/health").json()["disk_free_gb"] == 0.5
        free["n"] = int(0.5 * GB) - 1
        assert post(client, pdf_bytes()).status_code == 503
    assert job_count(settings) == 1


# ── AC-1: the rate window ─────────────────────────────────────────────────────────────────────────────


def test_client_ip_believes_x_forwarded_for_only_from_the_trusted_proxy() -> None:
    assert ratelimit.client_ip(request_from("203.0.113.9"), None) == "203.0.113.9"
    # A spoofed header from an untrusted peer changes nothing.
    assert ratelimit.client_ip(request_from("203.0.113.9", "10.0.0.1"), None) == "203.0.113.9"
    assert ratelimit.client_ip(request_from("203.0.113.9", "10.0.0.1"), "172.18.0.2") == "203.0.113.9"
    # From the proxy: the LAST hop is the one it appended; earlier ones are whatever the client sent.
    assert ratelimit.client_ip(request_from("172.18.0.2", "1.1.1.1, 198.51.100.7"), "172.18.0.2") == "198.51.100.7"
    assert ratelimit.client_ip(request_from("172.18.0.2", " 198.51.100.7 "), "172.18.0.2") == "198.51.100.7"
    assert ratelimit.client_ip(request_from("172.18.0.2", ""), "172.18.0.2") == "172.18.0.2"
    assert ratelimit.client_ip(request_from("172.18.0.2"), "172.18.0.2") == "172.18.0.2"


def test_the_trusted_proxy_setting_is_a_list_and_ipv6_spellings_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caddy has an IPv4 and an IPv6 address on the compose network and may connect over either (gate r1), so
    the setting names both; an IPv6 address matches however it is spelled, and the forwarded client may be v6."""
    both = "172.30.0.10, fd30:5eaf:9a13:0:0:0:0:10"
    assert ratelimit.trusted_proxies(both) == frozenset({"172.30.0.10", "fd30:5eaf:9a13::10"})
    assert ratelimit.trusted_proxies(None) == ratelimit.trusted_proxies(" , ") == frozenset()
    assert ratelimit.client_ip(request_from("172.30.0.10", "198.51.100.7"), both) == "198.51.100.7"
    assert ratelimit.client_ip(request_from("fd30:5eaf:9a13::10", "2001:db8::7"), both) == "2001:db8::7"
    assert ratelimit.client_ip(request_from("fd30:5eaf:9a13::10", "198.51.100.7"), both) == "198.51.100.7"
    # Any other peer, v4 or v6, is the client whatever it forwards.
    assert ratelimit.client_ip(request_from("fd30:5eaf:9a13::11", "2001:db8::7"), both) == "fd30:5eaf:9a13::11"
    assert ratelimit.client_ip(request_from("172.30.0.11", "2001:db8::7"), both) == "172.30.0.11"
    # Through Settings, from the environment, as compose sets it.
    monkeypatch.setenv("PDFSPLIT_TRUSTED_PROXY", both)
    assert ratelimit.trusted_proxies(Settings().trusted_proxy) == ratelimit.trusted_proxies(both)


def test_ipv6_clients_share_a_window_per_64_and_v4_mapped_addresses_are_their_v4() -> None:
    """A v6 visitor owns a /64 (or more), so keying on the full address would hand them 2^64 windows (gate r2):
    the key is the /64. Distinct /64s stay distinct; IPv4 is keyed as before; `::ffff:a.b.c.d` is a.b.c.d."""
    assert ratelimit.rate_key("2001:db8:1:2:3:4:5:6") == "2001:db8:1:2::/64"
    assert ratelimit.rate_key("2001:DB8:1:2::7") == ratelimit.rate_key("2001:db8:1:2:ffff:ffff:ffff:ffff")
    assert ratelimit.rate_key("::ffff:203.0.113.9") == ratelimit.rate_key("203.0.113.9") == "203.0.113.9"
    assert ratelimit.rate_key("testclient") == "testclient"
    # The whole /64 is one bucket: the hash — what `rate` rows and the in-flight cap key on — is the same.
    same = ratelimit.ip_hash("2001:db8:1:2::7", T0, "s")
    assert ratelimit.ip_hash("2001:db8:1:2:aaaa:bbbb:cccc:dddd", T0, "s") == same
    assert ratelimit.ip_hash("2001:db8:1:2::8", T0, "s") == same
    # A different /64 is a different client, as is the same host part in another prefix.
    assert ratelimit.ip_hash("2001:db8:1:3::7", T0, "s") != same
    assert ratelimit.ip_hash("2001:db8:1:2::/64", T0, "s") == same
    # IPv4: address for address, and the mapped spelling is not a way around it.
    v4 = ratelimit.ip_hash("203.0.113.9", T0, "s")
    assert ratelimit.ip_hash("::ffff:203.0.113.9", T0, "s") == v4
    assert ratelimit.ip_hash("203.0.113.10", T0, "s") != v4


def test_ip_hash_is_salted_shared_by_secret_and_rotates_daily() -> None:
    a = ratelimit.ip_hash("198.51.100.7", T0, "secret")
    assert len(a) == 64 and "198" not in a
    assert ratelimit.ip_hash("198.51.100.7", T0 + timedelta(hours=11, minutes=59), "secret") == a   # same UTC day
    assert ratelimit.ip_hash("198.51.100.7", T0 + timedelta(hours=12), "secret") != a              # the next day
    assert ratelimit.ip_hash("198.51.100.7", T0, "other secret") != a
    assert ratelimit.ip_hash("198.51.100.8", T0, "secret") != a
    # No secret: one drawn per process, stable within it.
    assert ratelimit.ip_hash("198.51.100.7", T0) == ratelimit.ip_hash("198.51.100.7", T0)
    assert ratelimit.ip_hash("198.51.100.7", T0) != a


def slot(store: Store, hashes: list[str], limit: int, now: datetime) -> int | None:
    return store.take_rate_slot(lambda at: (hashes, at - ratelimit.WINDOW), limit=limit, clock=lambda: now)[1]


def rate_rows(store: Store) -> list[tuple[str, str]]:
    return [tuple(r) for r in store.conn.execute("SELECT ip_hash, at FROM rate ORDER BY at, rowid")]


def test_rate_window_slides_and_says_how_long_to_wait(store: Store) -> None:
    """`Store.take_rate_slot`: a slot taken (None, and a row) or the wait until the oldest counted hit leaves."""
    h = ["hash-a"]
    for i in range(6):
        assert slot(store, h, 6, T0 + timedelta(minutes=i)) is None                 # 12:00 … 12:05
    assert slot(store, h, 6, T0 + timedelta(minutes=10)) == 50 * 60
    assert slot(store, h, 6, T0 + timedelta(minutes=59, seconds=59)) == 1
    assert len(rate_rows(store)) == 6                                              # a refusal records nothing
    assert slot(store, ["hash-b"], 6, T0 + timedelta(minutes=10)) is None
    assert slot(store, h, 6, T0 + timedelta(hours=1)) is None                      # 12:00 has left the window
    # The 13:00 hit refilled the window: the wait is until the OLDEST of the six in it (12:01) is out.
    assert slot(store, h, 6, T0 + timedelta(hours=1, seconds=1)) == 59
    assert slot(store, h, 6, T0 + timedelta(hours=1, minutes=1)) is None


def test_the_slot_is_taken_atomically_so_a_burst_from_one_client_gets_exactly_the_limit(tmp_path: Path) -> None:
    """Gate r1 finding 1: the count and the record were two statements, and parallel uploads from one client
    all read a window with room. Twelve stores (one per thread, like the api's per-request stores) released
    together by a barrier at limit 6: six slots, six refusals, six rows."""
    path = tmp_path / "jobs.db"
    Store(path).init()
    n, barrier = 12, threading.Barrier(12)
    results: list[int | None] = []
    lock = threading.Lock()

    def attempt() -> None:
        s = Store(path)
        try:
            barrier.wait()
            r = slot(s, ["hash-a"], 6, T0)
        finally:
            s.close()
        with lock:
            results.append(r)

    threads = [threading.Thread(target=attempt) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert results.count(None) == 6 and len(results) == n
    assert all(r == 3600 for r in results if r is not None)
    s = Store(path)
    try:
        assert len(rate_rows(s)) == 6
    finally:
        s.close()


def test_parallel_uploads_from_one_client_get_exactly_the_limit(settings: Settings) -> None:
    """The same burst through `POST /api/jobs` (the review saw 10/10 accepted at limit 6): six 201s, six 429s,
    six job directories."""
    n, barrier = 12, threading.Barrier(12)
    statuses: list[int] = []
    lock = threading.Lock()
    # The in-flight cap (STORY-013 gate r1) would answer 503 to most of the burst; this test is about the window.
    with TestClient(create_app(settings.model_copy(update={"max_uploads": n, "max_uploads_per_client": n}))) as client:

        def attempt() -> None:
            barrier.wait()
            r = post(client, pdf_bytes())
            with lock:
                statuses.append(r.status_code)

        threads = [threading.Thread(target=attempt) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
    assert sorted(statuses) == [201] * 6 + [429] * 6
    assert job_count(settings) == 6 and len(job_dirs(settings)) == 6


def test_window_hashes_reach_into_yesterday_only_during_the_first_hour() -> None:
    day = datetime(2026, 9, 26, 0, 0, 0, tzinfo=UTC)
    today, yesterday = ratelimit.ip_hash("ip", day, "s"), ratelimit.ip_hash("ip", day - timedelta(days=1), "s")
    assert today != yesterday
    assert ratelimit.window_hashes("ip", day, "s") == [today, yesterday]
    assert ratelimit.window_hashes("ip", day + timedelta(minutes=59, seconds=59), "s") == [today, yesterday]
    assert ratelimit.window_hashes("ip", day + timedelta(hours=1), "s") == [today]
    assert ratelimit.window_hashes("ip", day - timedelta(seconds=1), "s") == [yesterday]


def test_the_window_survives_midnight(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate r1 finding 2: the window was looked up under today's hash only, so it emptied at 00:00 UTC. On a
    frozen clock at `rate_per_hour=2`: an upload at 23:55, one at 00:06 (recorded under the NEW day's hash),
    and the third at 00:07 is 429 until the 23:55 hit is an hour old."""
    clock = {"now": datetime(2026, 9, 25, 23, 55, 0, tzinfo=UTC)}
    monkeypatch.setattr(ratelimit, "utcnow", lambda: clock["now"])
    limited = settings.model_copy(update={"rate_per_hour": 2})
    with TestClient(create_app(limited)) as client:
        first = post(client, pdf_bytes())
        assert first.status_code == 201
        clock["now"] = datetime(2026, 9, 26, 0, 6, 0, tzinfo=UTC)
        second = post(client, pdf_bytes())
        assert second.status_code == 201
        clock["now"] = datetime(2026, 9, 26, 0, 7, 0, tzinfo=UTC)
        r = post(client, pdf_bytes())
        assert r.status_code == 429, r.text
        assert int(r.headers["retry-after"]) == 48 * 60
        clock["now"] = datetime(2026, 9, 26, 0, 54, 59, tzinfo=UTC)
        r = post(client, pdf_bytes())
        assert r.status_code == 429 and r.headers["retry-after"] == "1"
        clock["now"] = datetime(2026, 9, 26, 0, 55, 0, tzinfo=UTC)                  # 23:55 is exactly an hour old
        assert post(client, pdf_bytes()).status_code == 201
    store = Store(settings.db_path)
    try:
        rows = rate_rows(store)
        hashes = {store.get_job(r.json()["id"])["ip_hash"] for r in (first, second)}
    finally:
        store.close()
    assert [at for _, at in rows] == ["2026-09-25T23:55:00+00:00", "2026-09-26T00:06:00+00:00", "2026-09-26T00:55:00+00:00"]
    assert len(hashes) == 2 and [h for h, _ in rows] == [rows[0][0], rows[1][0], rows[1][0]]    # the salt turned once
    assert job_count(settings) == 3


def test_take_rate_slot_reads_its_clock_and_builds_the_window_inside_the_transaction(store: Store) -> None:
    """Gate r2 finding 1: the clock, and with it the dated hash set, was read before `BEGIN IMMEDIATE`. Now the
    clock is asked once per attempt while the connection holds the write transaction, and the window is built
    from exactly that reading — the hit is stamped with it too."""
    readings: list[tuple[datetime, bool]] = []
    windows: list[datetime] = []
    ticks = iter(T0 + timedelta(seconds=i) for i in range(2))

    def clock() -> datetime:
        now = next(ticks)
        readings.append((now, store.conn.in_transaction))
        return now

    def window(now: datetime) -> tuple[list[str], datetime]:
        windows.append(now)
        return ["hash-a"], now - ratelimit.WINDOW

    assert store.take_rate_slot(window, limit=1, clock=clock) == ("hash-a", None)
    assert store.take_rate_slot(window, limit=1, clock=clock) == ("hash-a", 3599)
    assert readings == [(T0, True), (T0 + timedelta(seconds=1), True)]
    assert windows == [T0, T0 + timedelta(seconds=1)]
    assert rate_rows(store) == [("hash-a", "2026-09-25T12:00:00+00:00")]
    assert not store.conn.in_transaction


def test_a_request_that_wins_the_lock_after_midnight_counts_under_that_days_hashes(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate r2 finding 1, the reviewer's interleaving: six requests that read the clock at 00:00:00.05 commit
    first, then six that read it at 23:59:59.9 — they arrived before midnight and waited for the lock. A reading
    taken on arrival puts the second six under yesterday's hash alone, where the window is empty, and all
    twelve get through at limit 6. Read under the lock, the day has turned for every one of them: six slots."""
    before = datetime(2026, 9, 25, 23, 59, 59, 900000, tzinfo=UTC)
    after = datetime(2026, 9, 26, 0, 0, 0, 50000, tzinfo=UTC)
    arrival = {"at": after}
    # The clock answers with the request's arrival while no transaction is open, and with the moment the lock was
    # won (after midnight for all twelve) once one is: only a read under the lock sees the second answer.
    monkeypatch.setattr(ratelimit, "utcnow", lambda: after if store.conn.in_transaction else arrival["at"])
    results: list[tuple[str, int | None]] = []
    for at in [after] * 6 + [before] * 6:
        arrival["at"] = at
        results.append(ratelimit.take_slot(store, "198.51.100.7", 6, secret="s"))
    today = ratelimit.ip_hash("198.51.100.7", after, "s")
    assert [wait for _, wait in results] == [None] * 6 + [3600] * 6
    assert [h for h, _ in results] == [today] * 12
    assert rate_rows(store) == [(today, "2026-09-26T00:00:00+00:00")] * 6


def test_seventh_upload_in_an_hour_is_429_with_retry_after(tmp_path: Path) -> None:
    """PRD AC-11 at `rate_per_hour=2`: the third attempt is refused and told how long to wait; it leaves
    nothing behind. A refused file (not a PDF) counts as an attempt too."""
    settings = Settings(jobs_dir=tmp_path / "jobs", rate_per_hour=2)
    with TestClient(create_app(settings)) as client:
        assert post(client, pdf_bytes()).status_code == 201
        assert post(client, b"not a pdf").status_code == 400
        r = post(client, pdf_bytes())
        assert r.status_code == 429, r.text
        assert r.json() == {"code": "rate_limited", "message": upload.MESSAGES["rate_limited"]}
        assert 3500 < int(r.headers["retry-after"]) <= 3600
        assert r.headers["x-request-id"]
        # Another client (through the trusted proxy) is not affected.
        proxied = settings.model_copy(update={"trusted_proxy": "testclient"})
    assert job_count(settings) == 1 and len(job_dirs(settings)) == 1
    with TestClient(create_app(proxied)) as client:
        assert post(client, pdf_bytes(), headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 201
        assert post(client, pdf_bytes(), headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 201
        assert post(client, pdf_bytes(), headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 429
        assert post(client, pdf_bytes(), headers={"X-Forwarded-For": "198.51.100.8"}).status_code == 201
    assert job_count(settings) == 4


def test_rate_rows_and_job_rows_carry_the_same_hash_and_never_the_ip(settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with TestClient(create_app(settings.model_copy(update={"rate_per_hour": 1}))) as client:
        job_id = post(client, pdf_bytes()).json()["id"]
        assert post(client, pdf_bytes()).status_code == 429
    store = Store(settings.db_path)
    try:
        job = store.get_job(job_id)
        hits = store.conn.execute("SELECT ip_hash FROM rate").fetchall()
    finally:
        store.close()
    assert [r["ip_hash"] for r in hits] == [job["ip_hash"]]
    assert "testclient" not in caplog.text and job["ip_hash"] not in caplog.text
    assert "rate limited" in caplog.text


# ── AC-3: the janitor ─────────────────────────────────────────────────────────────────────────────────


def job_dir_with_files(settings: Settings, job_id: str) -> Path:
    d = settings.jobs_dir / job_id
    (d / "work").mkdir(parents=True)
    (d / "source.pdf").write_bytes(b"%PDF-")
    (d / "work" / "001-a.pdf").write_bytes(b"%PDF-")
    return d


@pytest.fixture
def jstore(settings: Settings):
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    s = Store(settings.db_path)
    s.init()
    yield s
    s.close()


def test_expired_jobs_are_marked_deleted_row_first_and_their_directories_removed(
    settings: Settings, jstore: Store, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    make(jstore, DASH_ID, now=T0, state="review")
    make(jstore, OTHER_ID, now=T0 + timedelta(hours=2), state="done", kind="cut")
    old, fresh = job_dir_with_files(settings, DASH_ID), job_dir_with_files(settings, OTHER_ID)
    assert janitor.sweep(jstore, settings, now=T0 + timedelta(hours=24)) == dict.fromkeys(janitor.COUNTS, 0)
    assert old.exists() and fresh.exists()                            # strictly `expires_at < now`
    counts = janitor.sweep(jstore, settings, now=T0 + timedelta(hours=25))
    assert counts["expired"] == 1 and not old.exists() and fresh.exists()
    assert jstore.get_job(DASH_ID)["state"] == "deleted"
    assert jstore.get_job(OTHER_ID)["state"] == "done"
    assert settings.db_path.exists()
    assert "janitor: 1 expired" in caplog.text
    assert_id_gone(caplog.text, DASH_ID)
    # Idempotent: nothing left for a second pass.
    assert janitor.sweep(jstore, settings, now=T0 + timedelta(hours=25)) == dict.fromkeys(janitor.COUNTS, 0)


def test_a_running_job_past_its_ttl_loses_its_row_before_its_directory(
    settings: Settings, jstore: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    make(jstore, DASH_ID, now=T0, state="running", kind="cut")
    job_dir_with_files(settings, DASH_ID)
    order: list[str] = []
    real_rmtree = shutil.rmtree

    def spy(path, *a, **kw):
        order.append(f"rmtree:{jstore.get_job(DASH_ID)['state']}")
        return real_rmtree(path, *a, **kw)

    monkeypatch.setattr(janitor.shutil, "rmtree", spy)
    janitor.sweep(jstore, settings, now=T0 + timedelta(days=2))
    assert order == ["rmtree:deleted"]                                # the task then finds the row not running


def test_a_deleted_rows_directory_that_came_back_is_removed_again(settings: Settings, jstore: Store) -> None:
    """STORY-007 addendum: a late `mkdir(parents=True)` from an engine subprocess must not outlive the DELETE."""
    make(jstore, DASH_ID, now=T0, state="review")
    jstore.set_state(DASH_ID, "deleted", now=T0 + timedelta(minutes=5))
    back = job_dir_with_files(settings, DASH_ID)                       # a fresh mtime: no orphan grace applies
    counts = janitor.sweep(jstore, settings, now=T0 + timedelta(minutes=6))
    assert counts["recreated"] == 1 and not back.exists()
    assert jstore.get_job(DASH_ID)["state"] == "deleted"


def test_orphan_directories_are_removed_after_a_grace_and_the_database_never(settings: Settings, jstore: Store) -> None:
    stale = job_dir_with_files(settings, "orphanAbCdEfGhIjKlMn01")
    young = job_dir_with_files(settings, "orphanAbCdEfGhIjKlMn02")
    make(jstore, DASH_ID, now=T0)
    owned = job_dir_with_files(settings, DASH_ID)
    for d in (stale, owned):
        t = (T0 - timedelta(hours=2)).timestamp()
        os.utime(d, (t, t))
    t = (T0 - timedelta(minutes=5)).timestamp()
    os.utime(young, (t, t))                                            # an upload still copying its file
    stray_file = settings.jobs_dir / "jobs.db-journal"
    stray_file.write_bytes(b"")
    before = sorted(p.name for p in settings.jobs_dir.iterdir() if p.is_file())
    counts = janitor.sweep(jstore, settings, now=T0)
    assert counts["orphans"] == 1
    assert not stale.exists() and young.exists() and owned.exists()
    assert sorted(p.name for p in settings.jobs_dir.iterdir() if p.is_file()) == before
    assert janitor.sweep(jstore, settings, now=T0 + janitor.ORPHAN_GRACE)["orphans"] == 1
    assert not young.exists()


def test_the_spool_directory_is_never_reaped_only_stale_files_inside_it(settings: Settings, jstore: Store) -> None:
    """`<jobs>/.spool` has no row and can be hours old, yet it is the api's, not an orphan; a spooled part that
    outlived its request (the api died mid-upload) goes after the same grace, a fresh one stays."""
    spool = settings.spool_dir
    spool.mkdir()
    stale, fresh, sub = spool / "tmpabc", spool / "tmpdef", spool / "dir"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"y")
    sub.mkdir()
    old, recent = (T0 - timedelta(hours=2)).timestamp(), (T0 - timedelta(minutes=5)).timestamp()
    for p in (spool, stale, sub):
        os.utime(p, (old, old))
    os.utime(fresh, (recent, recent))
    counts = janitor.sweep(jstore, settings, now=T0)
    assert (counts["orphans"], counts["spool"]) == (0, 1)
    assert spool.is_dir() and not stale.exists() and fresh.exists() and sub.is_dir()
    assert janitor.sweep(jstore, settings, now=T0)["spool"] == 0
    assert janitor.sweep(jstore, settings, now=T0 + janitor.ORPHAN_GRACE)["spool"] == 1
    assert not fresh.exists()
    # No spool directory at all (a worker-only host) is not an error.
    shutil.rmtree(spool)
    assert janitor.sweep(jstore, settings, now=T0)["spool"] == 0


def test_rate_rows_out_of_the_window_and_week_old_deleted_rows_are_pruned(settings: Settings, jstore: Store) -> None:
    for minutes in (0, 30, 61):
        jstore.add_rate("h", T0 + timedelta(minutes=minutes))
    make(jstore, DASH_ID, now=T0 - timedelta(days=10), state="review")
    jstore.set_state(DASH_ID, "deleted", now=T0 - timedelta(days=8))
    make(jstore, OTHER_ID, now=T0 - timedelta(days=10), state="review")
    jstore.set_state(OTHER_ID, "deleted", now=T0 - timedelta(days=6))
    gone_dir = job_dir_with_files(settings, DASH_ID)
    counts = janitor.sweep(jstore, settings, now=T0 + timedelta(minutes=61))
    assert (counts["rate"], counts["rows"], counts["recreated"]) == (1, 1, 1)
    assert jstore.conn.execute("SELECT COUNT(*) FROM rate").fetchone()[0] == 2
    assert jstore.get_job(DASH_ID) is None and not gone_dir.exists()   # the directory went before the row
    assert jstore.get_job(OTHER_ID)["state"] == "deleted"
    assert janitor.sweep(jstore, settings, now=T0 + timedelta(hours=2))["rate"] == 1


def test_one_failure_does_not_stop_the_pass(settings: Settings, jstore: Store, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    make(jstore, DASH_ID, now=T0, state="review")
    make(jstore, OTHER_ID, now=T0, state="review")
    stubborn, other = job_dir_with_files(settings, DASH_ID), job_dir_with_files(settings, OTHER_ID)
    real_rmtree = shutil.rmtree

    def flaky(path, *a, **kw):
        if Path(path) == stubborn:
            raise PermissionError(13, "busy")
        return real_rmtree(path, *a, **kw)

    monkeypatch.setattr(janitor.shutil, "rmtree", flaky)
    counts = janitor.sweep(jstore, settings, now=T0 + timedelta(days=2))
    assert counts["expired"] == 2 and not other.exists() and stubborn.exists()
    assert jstore.get_job(DASH_ID)["state"] == jstore.get_job(OTHER_ID)["state"] == "deleted"
    assert "directory not removed: PermissionError" in caplog.text
    assert_id_gone(caplog.text, DASH_ID)
    monkeypatch.setattr(janitor.shutil, "rmtree", real_rmtree)
    assert janitor.sweep(jstore, settings, now=T0 + timedelta(days=2))["recreated"] == 1   # retried next pass
    assert not stubborn.exists()


def test_the_worker_runs_the_janitor_on_start_and_every_interval(
    settings: Settings, jstore: Store, analyzed_template: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wiring: `Runner.serve` sweeps at start, then every `JANITOR_EVERY_S`, on its own thread."""
    monkeypatch.setattr(runner_mod, "JANITOR_EVERY_S", 0.1)
    expired = job_dir_with_files(settings, OTHER_ID)
    make(jstore, OTHER_ID, now=datetime.now(UTC) - timedelta(days=2), state="review")
    seed_job(settings, analyzed_template, DASH_ID)                    # alive: a job the SPA could be on
    t, stop = serve_in_thread(Runner(settings, poll=0.05))
    try:
        wait_for(lambda: not expired.exists())
        assert jstore.get_job(OTHER_ID)["state"] == "deleted"
        # Expire the live one while the worker runs: the next pass takes it.
        jstore.conn.execute("UPDATE jobs SET expires_at = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", DASH_ID))
        wait_for(lambda: not (settings.jobs_dir / DASH_ID).exists())
        assert jstore.get_job(DASH_ID)["state"] == "deleted"
    finally:
        stop.set()
        t.join(10)
    assert not t.is_alive()
