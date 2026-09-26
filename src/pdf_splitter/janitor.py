"""The janitor (Architecture § janitor): what the 24 h retention and the rate window leave behind, swept every
5 min from the worker process (`worker/runner.py`).

One pass, in this order, each step on its own so a failure in one job never stops the rest:
  1. rows past `expires_at` → `deleted` FIRST, then their directory (the same order as DELETE /api/jobs/{id}:
     a task still running on the job then finds its row not `running` and writes nothing new);
  2. every `deleted` row's directory, should a late write have brought it back (a defence, so cheap it runs
     every pass);
  3. directories under the jobs dir with no row at all, unless they are younger than `ORPHAN_GRACE` — an upload
     makes its directory BEFORE its row exists (upload.py) and must not lose it mid-copy;
  4. `rate` rows out of the window, and `deleted` rows older than `KEEP_DELETED` (their directories went in 1–2).
Everything takes `now`, so the tests never sleep. Job ids reach the log only as `log_id` (ADR-007).
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import Settings
from .ratelimit import WINDOW
from .store import Store, log_id

log = logging.getLogger(__name__)

KEEP_DELETED = timedelta(days=7)
ORPHAN_GRACE = timedelta(hours=1)
COUNTS = ("expired", "recreated", "orphans", "rate", "rows")


def sweep(store: Store, settings: Settings, now: datetime | None = None) -> dict[str, int]:
    """One pass; returns how many of each the pass removed. Idempotent: a second pass right after finds nothing."""
    now = now or datetime.now(UTC)
    counts = dict.fromkeys(COUNTS, 0)
    for job in store.expired(now):
        if job["state"] == "deleted":
            continue
        if _delete(store, settings, job, now):
            counts["expired"] += 1
    for job in store.deleted():
        path = settings.jobs_dir / job["id"]
        if path.is_dir() and _rmtree(path, job["id"]):
            counts["recreated"] += 1
    counts["orphans"] = _reap_orphans(store, settings, now)
    counts["rate"] = _prune(store.prune_rate, now - WINDOW, "rate rows")
    counts["rows"] = _prune(store.prune_deleted, now - KEEP_DELETED, "deleted rows")
    if any(counts.values()):
        log.info("janitor: %s", ", ".join(f"{n} {k}" for k, n in counts.items()))
    return counts


def _delete(store: Store, settings: Settings, job: dict, now: datetime) -> bool:
    try:
        store.set_state(job["id"], "deleted", now=now)
    except Exception as e:  # noqa: BLE001 - e.g. sqlite busy past its timeout; the next pass retries
        log.warning("janitor: job %s could not be marked deleted: %s", log_id(job["id"]), type(e).__name__)
        return False
    _rmtree(settings.jobs_dir / job["id"], job["id"])
    return True


def _rmtree(path: Path, job_id: str) -> bool:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        return False
    except OSError as e:
        log.warning("janitor: job %s directory not removed: %s", log_id(job_id), type(e).__name__)
        return False
    return True


def _reap_orphans(store: Store, settings: Settings, now: datetime) -> int:
    removed = 0
    try:
        entries = list(settings.jobs_dir.iterdir())
    except OSError as e:
        log.warning("janitor: jobs dir not listed: %s", type(e).__name__)
        return 0
    for path in entries:
        # `jobs.db`, its WAL and shm are files, never candidates; only directories can be jobs.
        if not path.is_dir():
            continue
        try:
            age = now - datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        if age < ORPHAN_GRACE or store.get_job(path.name) is not None:
            continue
        if _rmtree(path, path.name):
            removed += 1
    return removed


def _prune(prune, before: datetime, what: str) -> int:
    try:
        return prune(before)
    except Exception as e:  # noqa: BLE001 - the next pass retries
        log.warning("janitor: %s not pruned: %s", what, type(e).__name__)
        return 0
