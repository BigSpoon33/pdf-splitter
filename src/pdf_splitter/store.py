from __future__ import annotations

import hashlib
import logging
import math
import secrets
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id          TEXT PRIMARY KEY,
  state       TEXT NOT NULL,
  kind        TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  started_at  TEXT,
  ip_hash     TEXT NOT NULL,
  owner       TEXT,
  filename    TEXT NOT NULL,
  bytes       INTEGER NOT NULL,
  pages       INTEGER NOT NULL,
  progress    INTEGER NOT NULL DEFAULT 0,
  total       INTEGER NOT NULL DEFAULT 0,
  message     TEXT,
  error_code  TEXT
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state, created_at);
CREATE INDEX IF NOT EXISTS jobs_expiry ON jobs(expires_at);
CREATE TABLE IF NOT EXISTS rate (ip_hash TEXT, at TEXT);
CREATE INDEX IF NOT EXISTS rate_window ON rate(ip_hash, at);
"""

STATES = frozenset({"queued", "running", "review", "done", "failed", "deleted"})
KINDS = frozenset({"analyze", "cut"})
BUSY_TIMEOUT_S = 5.0

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    """The clock a write reads while it holds the database lock; `ratelimit` re-exports it as the seam a test freezes."""
    return datetime.now(UTC)


def now_ts(now: datetime | None = None) -> str:
    """One fixed UTC format everywhere, so TEXT comparison and ORDER BY are time order."""
    return (now or utcnow()).astimezone(UTC).isoformat(timespec="seconds")


def new_job_id() -> str:
    return secrets.token_urlsafe(16)


def log_id(job_id: str) -> str:
    """The job id is the only credential (ADR-007), so logs carry this short hash instead."""
    return hashlib.sha256(job_id.encode()).hexdigest()[:8]


class Store:
    """The jobs table in SQLite WAL mode, shared by api and worker through the jobs volume."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        # isolation_level=None: we issue BEGIN IMMEDIATE ourselves; Python's implicit
        # transactions would otherwise start a deferred one and defeat the claim lock.
        # check_same_thread=False: FastAPI opens the per-request Store in the endpoint's worker
        # thread but runs the dependency teardown (close) as a separate threadpool call, often on
        # another thread. A Store is still used by ONE request/worker at a time, never shared
        # concurrently, so dropping the thread check is safe.
        conn = sqlite3.connect(
            self.path, timeout=BUSY_TIMEOUT_S, isolation_level=None, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_S * 1000)}")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = self.connect()
        return self._conn

    def close(self) -> None:
        if self._conn is None:
            return
        # Always drop the reference: a close that raises must not leave a leaked, half-dead
        # connection (and its WAL/shm fds) attached to this Store.
        try:
            self._conn.close()
        finally:
            self._conn = None

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn.executescript(SCHEMA)

    def journal_mode(self) -> str:
        return self.conn.execute("PRAGMA journal_mode").fetchone()[0]

    def create_job(
        self,
        *,
        ip_hash: str,
        filename: str,
        bytes: int,
        pages: int,
        ttl_hours: int,
        kind: str = "analyze",
        state: str = "queued",
        job_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        _check(state=state, kind=kind)
        job_id = job_id or new_job_id()
        created = now or datetime.now(UTC)
        ts = now_ts(created)
        self.conn.execute(
            "INSERT INTO jobs (id, state, kind, created_at, updated_at, expires_at, ip_hash,"
            " filename, bytes, pages) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id, state, kind, ts, ts, now_ts(created + timedelta(hours=ttl_hours)),
                ip_hash, filename, bytes, pages,
            ),
        )
        log.info("job %s created (%s/%s)", log_id(job_id), state, kind)
        job = self.get_job(job_id)
        assert job is not None
        return job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def queue_length(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM jobs WHERE state = 'queued'").fetchone()[0]

    def queue_position(self, job: dict[str, Any]) -> int | None:
        """1 + the queued jobs of the same kind ahead of this one in `claim_next`'s order; None unless queued."""
        if job["state"] != "queued":
            return None
        # Timestamps are whole seconds, so jobs created in the same second tie on `created_at`; the rowid (the
        # insertion order) breaks the tie exactly as `claim_next` does.
        ahead = self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE state = 'queued' AND kind = ? AND (created_at < ? OR"
            " (created_at = ? AND rowid < (SELECT rowid FROM jobs WHERE id = ?)))",
            (job["kind"], job["created_at"], job["created_at"], job["id"]),
        ).fetchone()[0]
        return 1 + ahead

    def claim_next(self, kind: str, now: datetime | None = None) -> dict[str, Any] | None:
        """Atomically move the oldest queued job of `kind` to running; None if the queue is empty."""
        _check(kind=kind)
        ts = now_ts(now)
        conn = self.conn
        # BEGIN IMMEDIATE takes the write lock up front, so two claimers serialize here
        # instead of both reading the same queued row.
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "UPDATE jobs SET state = 'running', started_at = ?, updated_at = ?"
                " WHERE id = (SELECT id FROM jobs WHERE state = 'queued' AND kind = ?"
                " ORDER BY created_at, rowid LIMIT 1) RETURNING *",
                (ts, ts, kind),
            ).fetchone()
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        if row is None:
            return None
        log.info("job %s claimed (%s)", log_id(row["id"]), kind)
        return dict(row)

    def update_progress(
        self, job_id: str, progress: int, total: int, message: str | None = None, now: datetime | None = None
    ) -> None:
        self.conn.execute(
            "UPDATE jobs SET progress = ?, total = ?, message = ?, updated_at = ? WHERE id = ?",
            (progress, total, message, now_ts(now), job_id),
        )

    def set_state(
        self,
        job_id: str,
        state: str,
        *,
        kind: str | None = None,
        error_code: str | None = None,
        message: str | None = None,
        now: datetime | None = None,
    ) -> None:
        _check(state=state, kind=kind)
        self.conn.execute(
            "UPDATE jobs SET state = ?, kind = COALESCE(?, kind), error_code = ?, message = ?,"
            " updated_at = ? WHERE id = ?",
            (state, kind, error_code, message, now_ts(now), job_id),
        )
        log.info("job %s -> %s%s", log_id(job_id), state, f" ({error_code})" if error_code else "")

    def transition(
        self,
        job_id: str,
        expect: str | tuple[str, ...],
        state: str,
        *,
        kind: str | None = None,
        expect_kind: str | None = None,
        error_code: str | None = None,
        message: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """`set_state` only if the row is still in `expect` (one state or several, and `expect_kind` when given);
        False otherwise. The worker finishes jobs with this, so a job deleted (or already finished) while its
        subprocess ran is never resurrected, and the api queues a cut with it, so two clients can't queue the
        same job twice or re-queue one a worker has already claimed."""
        expected = (expect,) if isinstance(expect, str) else tuple(expect)
        _check(state=state, kind=kind)
        _check(kind=expect_kind)
        for e in expected:
            _check(state=e)
        # A queued cut starts from a clean progress bar, not the analyze's final count.
        cur = self.conn.execute(
            "UPDATE jobs SET state = ?, kind = COALESCE(?, kind), error_code = ?, message = ?, updated_at = ?,"
            " progress = CASE WHEN ? = 'queued' THEN 0 ELSE progress END,"
            " total = CASE WHEN ? = 'queued' THEN 0 ELSE total END"
            f" WHERE id = ? AND state IN ({','.join('?' * len(expected))}) AND kind = COALESCE(?, kind)",
            (state, kind, error_code, message, now_ts(now), state, state, job_id, *expected, expect_kind),
        )
        if cur.rowcount:
            log.info("job %s -> %s%s", log_id(job_id), state, f" ({error_code})" if error_code else "")
        return cur.rowcount > 0

    def running_since(self, kind: str, before: datetime) -> list[dict[str, Any]]:
        """Jobs of `kind` that have been `running` since before `before` (oldest first)."""
        _check(kind=kind)
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE state = 'running' AND kind = ? AND started_at < ? ORDER BY started_at",
            (kind, now_ts(before)),
        ).fetchall()
        return [dict(r) for r in rows]

    def expired(self, now: datetime | None = None) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE expires_at < ? ORDER BY expires_at", (now_ts(now),)
        ).fetchall()
        return [dict(r) for r in rows]

    def deleted(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM jobs WHERE state = 'deleted' ORDER BY updated_at").fetchall()
        return [dict(r) for r in rows]

    def prune_deleted(self, before: datetime) -> int:
        """Drop `deleted` rows last touched before `before`; their directories are the janitor's, first."""
        return self.conn.execute(
            "DELETE FROM jobs WHERE state = 'deleted' AND updated_at < ?", (now_ts(before),)
        ).rowcount

    # ── the rate window (AC-1) ──────────────────────────────────────────────────────────────────────────

    def add_rate(self, ip_hash: str, now: datetime | None = None) -> None:
        self.conn.execute("INSERT INTO rate (ip_hash, at) VALUES (?, ?)", (ip_hash, now_ts(now)))

    def take_rate_slot(
        self,
        window: Callable[[datetime], tuple[Sequence[str], datetime]],
        *,
        limit: int,
        clock: Callable[[], datetime] = utcnow,
    ) -> tuple[str, int | None]:
        """Read the clock, ask `window(now)` for the hashes to count and the `since` the window starts at, count
        the hits under any of them after `since` (a hit exactly that old has left the window) and, while there
        are fewer than `limit`, record one under the first hash — all of it in ONE write transaction, so parallel
        uploads from one client can't each read a window with room in it and all get through. The clock is read
        AFTER the lock is taken: the hashes are dated, so a request that arrived before midnight but reached the
        lock after the day turned must count under the hashes of the moment it commits, or a burst straddling
        midnight gets two windows. Returns the first hash (what a hit is recorded under) and None when the slot
        was taken, else the whole seconds until the oldest of the `limit` newest hits leaves the window."""
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            now = clock()
            hashes, since = window(now)
            marks = ",".join("?" * len(hashes))
            rows = conn.execute(
                f"SELECT at FROM rate WHERE ip_hash IN ({marks}) AND at > ? ORDER BY at",
                (*hashes, now_ts(since)),
            ).fetchall()
            if len(rows) >= limit:
                conn.execute("ROLLBACK")
                oldest = datetime.fromisoformat(rows[len(rows) - limit]["at"])
                return hashes[0], max(1, math.ceil((oldest - since).total_seconds()))
            conn.execute("INSERT INTO rate (ip_hash, at) VALUES (?, ?)", (hashes[0], now_ts(now)))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return hashes[0], None

    def prune_rate(self, before: datetime) -> int:
        return self.conn.execute("DELETE FROM rate WHERE at < ?", (now_ts(before),)).rowcount


def _check(*, state: str | None = None, kind: str | None = None) -> None:
    if state is not None and state not in STATES:
        raise ValueError(f"unknown job state {state!r}")
    if kind is not None and kind not in KINDS:
        raise ValueError(f"unknown job kind {kind!r}")
