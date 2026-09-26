"""STORY-012: the limits — the disk guard (AC-2), the rate window (AC-1), the janitor (AC-3 + the STORY-007
addendum) — every clock frozen (AC-5), so nothing here sleeps."""

from __future__ import annotations

import logging
import os
import shutil
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


def test_rate_window_slides_and_says_how_long_to_wait(store: Store) -> None:
    h = "hash-a"
    for i in range(6):
        ratelimit.record(store, h, T0 + timedelta(minutes=i))                 # 12:00 … 12:05
    assert ratelimit.retry_after(store, h, 6, T0 + timedelta(minutes=10)) == 50 * 60
    assert ratelimit.retry_after(store, h, 6, T0 + timedelta(minutes=59, seconds=59)) == 1
    assert ratelimit.retry_after(store, h, 6, T0 + timedelta(hours=1)) is None        # 12:00 has left the window
    assert ratelimit.retry_after(store, h, 7, T0 + timedelta(minutes=10)) is None
    assert ratelimit.retry_after(store, "hash-b", 6, T0 + timedelta(minutes=10)) is None
    # Another hit at 13:00 refills the window: the wait is until the OLDEST of the six in it (12:01) is out.
    ratelimit.record(store, h, T0 + timedelta(hours=1))
    assert ratelimit.retry_after(store, h, 6, T0 + timedelta(hours=1, seconds=1)) == 59
    assert ratelimit.retry_after(store, h, 6, T0 + timedelta(hours=1, minutes=1)) is None


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
