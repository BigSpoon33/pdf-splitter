from __future__ import annotations

import base64
import logging
import re
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from pdf_splitter.store import Store, log_id, new_job_id, now_ts

T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)


def make(store: Store, *, kind: str = "analyze", now: datetime = T0, **kw) -> dict:
    return store.create_job(
        ip_hash="iphash", filename="book.pdf", bytes=1234, pages=10, ttl_hours=24, kind=kind, now=now, **kw
    )


def test_wal_mode(store: Store) -> None:
    assert store.journal_mode() == "wal"
    assert store.conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"


def test_schema_matches_architecture(store: Store) -> None:
    cols = {r["name"]: r for r in store.conn.execute("PRAGMA table_info(jobs)")}
    assert list(cols) == [
        "id", "state", "kind", "created_at", "updated_at", "expires_at", "started_at", "ip_hash", "owner",
        "filename", "bytes", "pages", "progress", "total", "message", "error_code",
    ]
    not_null = {n for n, r in cols.items() if r["notnull"]}
    assert not_null == {
        "state", "kind", "created_at", "updated_at", "expires_at", "ip_hash", "filename", "bytes", "pages",
        "progress", "total",
    }
    assert cols["id"]["pk"] == 1
    indexes = {r["name"] for r in store.conn.execute("PRAGMA index_list(jobs)")}
    assert {"jobs_queue", "jobs_expiry"} <= indexes
    rate_cols = [r["name"] for r in store.conn.execute("PRAGMA table_info(rate)")]
    assert rate_cols == ["ip_hash", "at"]


def test_init_is_idempotent(store: Store) -> None:
    make(store)
    store.init()
    assert store.queue_length() == 1


def test_job_id_shape() -> None:
    ids = {new_job_id() for _ in range(200)}
    assert len(ids) == 200
    for job_id in ids:
        assert re.fullmatch(r"[A-Za-z0-9_-]{22}", job_id)
        assert len(base64.urlsafe_b64decode(job_id + "==")) == 16


def test_log_id_is_short_sha256() -> None:
    assert log_id("abc") == "ba7816bf"
    assert re.fullmatch(r"[0-9a-f]{8}", log_id(new_job_id()))


def test_logs_never_contain_the_raw_id(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="pdf_splitter")
    job = make(store)
    store.claim_next("analyze")
    store.update_progress(job["id"], 1, 2, "x")
    store.set_state(job["id"], "failed", error_code="timeout")
    assert caplog.records
    assert all(job["id"] not in r.getMessage() for r in caplog.records)
    assert any(log_id(job["id"]) in r.getMessage() for r in caplog.records)


def test_create_and_get(store: Store) -> None:
    job = make(store)
    assert re.fullmatch(r"[A-Za-z0-9_-]{22}", job["id"])
    assert job["state"] == "queued" and job["kind"] == "analyze"
    assert job["created_at"] == job["updated_at"] == now_ts(T0) == "2026-09-25T12:00:00+00:00"
    assert job["expires_at"] == now_ts(T0 + timedelta(hours=24))
    assert (job["progress"], job["total"], job["owner"], job["started_at"]) == (0, 0, None, None)
    assert (job["filename"], job["bytes"], job["pages"], job["ip_hash"]) == ("book.pdf", 1234, 10, "iphash")
    assert store.get_job(job["id"]) == job
    assert store.get_job(new_job_id()) is None


def test_create_rejects_unknown_state_or_kind(store: Store) -> None:
    with pytest.raises(ValueError):
        make(store, kind="split")
    with pytest.raises(ValueError):
        make(store, state="bogus")


def test_claim_next_is_fifo_per_kind(store: Store) -> None:
    a = make(store, now=T0)
    b = make(store, now=T0 + timedelta(seconds=1))
    c = make(store, kind="cut", now=T0 - timedelta(seconds=5))
    claimed = store.claim_next("analyze", now=T0 + timedelta(minutes=1))
    assert claimed is not None and claimed["id"] == a["id"]
    assert claimed["state"] == "running"
    assert claimed["started_at"] == claimed["updated_at"] == now_ts(T0 + timedelta(minutes=1))
    assert store.claim_next("analyze")["id"] == b["id"]
    assert store.claim_next("analyze") is None
    assert store.claim_next("cut")["id"] == c["id"]
    assert store.claim_next("cut") is None


def test_concurrent_claimers_never_share_a_job(tmp_path: Path) -> None:
    path = tmp_path / "jobs.db"
    setup = Store(path)
    setup.init()
    n_jobs, n_threads = 20, 8
    queued = {make(setup, now=T0 + timedelta(seconds=i))["id"] for i in range(n_jobs)}
    setup.close()

    barrier = threading.Barrier(n_threads)
    claimed: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        # sqlite3 connections are thread-bound, so each claimer gets its own Store.
        store = Store(path)
        try:
            barrier.wait()
            while (job := store.claim_next("analyze")) is not None:
                with lock:
                    claimed.append(job["id"])
        except BaseException as e:  # noqa: BLE001 - re-raised on the main thread, where pytest sees it
            errors.append(e)
        finally:
            store.close()

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(claimed) == len(set(claimed)) == n_jobs
    assert set(claimed) == queued


def test_connection_survives_cross_thread_close(tmp_path: Path) -> None:
    # FastAPI enters the per-request Store on one threadpool thread and tears it down on another.
    store = Store(tmp_path / "jobs.db")
    errors: list[BaseException] = []
    opened, closed = threading.Event(), threading.Event()

    def guarded(fn) -> None:
        try:
            fn()
        except BaseException as e:  # noqa: BLE001 - surfaced on the main thread
            errors.append(e)

    def open_and_use() -> None:
        store.init()
        make(store)
        opened.set()
        # Stay alive until the other thread has closed: a finished thread's id can be reused by
        # the next one, which would let sqlite's same-thread check pass by accident.
        closed.wait(timeout=10)

    def close_elsewhere() -> None:
        opened.wait(timeout=10)
        try:
            store.close()
        finally:
            closed.set()

    threads = [threading.Thread(target=guarded, args=(fn,)) for fn in (open_and_use, close_elsewhere)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert store._conn is None
    check = Store(tmp_path / "jobs.db")
    assert check.queue_length() == 1
    check.close()


def test_close_always_drops_the_connection(tmp_path: Path) -> None:
    class Boom(RuntimeError):
        pass

    class FailingConn:
        def close(self) -> None:
            raise Boom

    store = Store(tmp_path / "jobs.db")
    store._conn = FailingConn()  # type: ignore[assignment]
    with pytest.raises(Boom):
        store.close()
    assert store._conn is None
    store.close()  # a second close is a no-op, not another Boom


def test_update_progress(store: Store) -> None:
    job = make(store)
    later = T0 + timedelta(seconds=30)
    store.update_progress(job["id"], 3, 7, "Chapter 3", now=later)
    got = store.get_job(job["id"])
    assert (got["progress"], got["total"], got["message"]) == (3, 7, "Chapter 3")
    assert got["updated_at"] == now_ts(later)


def test_set_state(store: Store) -> None:
    job = make(store)
    store.set_state(job["id"], "review")
    got = store.get_job(job["id"])
    assert (got["state"], got["kind"]) == ("review", "analyze")
    store.set_state(job["id"], "queued", kind="cut")
    got = store.get_job(job["id"])
    assert (got["state"], got["kind"]) == ("queued", "cut")
    store.set_state(job["id"], "failed", error_code="timeout", message="The cut took too long.")
    got = store.get_job(job["id"])
    assert (got["state"], got["kind"], got["error_code"], got["message"]) == (
        "failed", "cut", "timeout", "The cut took too long.",
    )
    with pytest.raises(ValueError):
        store.set_state(job["id"], "exploded")


def test_expired_without_sleeping(store: Store) -> None:
    old = make(store, now=T0)
    fresh = make(store, now=T0 + timedelta(hours=2))
    assert store.expired(now=T0 + timedelta(hours=23)) == []
    assert store.expired(now=T0 + timedelta(hours=24)) == []  # strictly expires_at < now
    ids = [j["id"] for j in store.expired(now=T0 + timedelta(hours=25))]
    assert ids == [old["id"]]
    ids = [j["id"] for j in store.expired(now=T0 + timedelta(hours=27))]
    assert ids == [old["id"], fresh["id"]]


def test_timestamps_normalize_to_utc() -> None:
    pst = timezone(timedelta(hours=-8))
    assert now_ts(datetime(2026, 9, 25, 4, 0, 0, tzinfo=pst)) == "2026-09-25T12:00:00+00:00"
