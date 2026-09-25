"""`pdf-splitter worker`: claim queued jobs and run each in a sandboxed subprocess, ≤ WORKERS at a time.

Every job is `sandbox.command(limits, task(kind, id))`: rlimits (AS 2 GB, CPU timeout + 10 s, FSIZE 1 GB)
set by the launcher, then a wall-clock timeout here. Outcome → row: the task itself moves a good job to
`review`; a wall timeout → `failed/timeout`; death by an rlimit signal or a `resources` result →
`failed/resources`; anything else → `failed/internal`. Job ids never reach the log except as `log_id`
(ADR-007).
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ..access_log import redact_path
from ..config import Settings
from ..store import Store, log_id
from . import sandbox

log = logging.getLogger(__name__)

# (kind, job id) -> the Python args the sandbox launcher execs. Injectable so tests can run a fake task.
TaskArgs = Callable[[str, str], list[str]]

POLL_S = 1.0
SWEEP_EVERY_S = 30.0
STDERR_TAIL = 800
REQUEUED = "requeued"          # error_code marker on a row the sweep has already re-queued once
# RLIMIT_CPU ends a process with SIGXCPU (soft) or SIGKILL (hard; also the kernel OOM killer). Python
# ignores SIGXFSZ, but a task that re-enables it dies by it.
RESOURCE_SIGNALS = frozenset({signal.SIGXCPU, signal.SIGKILL, signal.SIGXFSZ})
MESSAGES = {
    "timeout": "The job took too long and was stopped.",
    "resources": "The PDF needed more memory or processing than allowed.",
    "internal": "Something went wrong while processing the PDF.",
}


def task_args(kind: str, job_id: str) -> list[str]:
    # `--`: 1 in 64 ids starts with `-`, and must never parse as an option.
    return ["-m", "pdf_splitter.task", kind, "--", job_id]


def loggable_tail(stderr: bytes) -> str:
    """The stderr tail, safe for a log line: ids hashed (a traceback names `<jobs_dir>/<id>/…`) BEFORE
    truncating, so the cut can't leave a short id fragment the run rule would miss, then escaped so MuPDF
    text or control bytes can't reach a terminal raw."""
    text = redact_path(stderr.decode("utf-8", errors="replace"))
    return json.dumps(text[-STDERR_TAIL:])


def classify(returncode: int, stdout: bytes) -> str | None:
    """None for success, else the failure code. The result is the last stdout line (see worker/task.py)."""
    try:
        result = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        result = None
    if returncode == 0 and isinstance(result, dict) and result.get("ok") is True:
        return None
    if returncode < 0 and -returncode in RESOURCE_SIGNALS:
        return "resources"
    if isinstance(result, dict) and result.get("code") == "resources":
        return "resources"
    return "internal"


class Runner:
    def __init__(
        self,
        settings: Settings,
        task: TaskArgs = task_args,
        kinds: tuple[str, ...] = ("analyze",),
        poll: float = POLL_S,
    ) -> None:
        self.settings = settings
        self.task = task
        self.kinds = kinds
        self.poll = poll
        self._in_flight: set[str] = set()
        self._lock = threading.Lock()
        self._last_sweep = 0.0

    def timeout(self, kind: str) -> int:
        return {"analyze": self.settings.analyze_timeout, "cut": self.settings.cut_timeout}[kind]

    # ── one job ──────────────────────────────────────────────────────────────────────────────────────────

    def execute(self, store: Store, job: dict[str, Any]) -> str | None:
        """Run one claimed job to its final row state; returns the failure code, None on success."""
        job_id, kind = job["id"], job["kind"]
        with self._lock:
            self._in_flight.add(job_id)
        try:
            code, rc, stderr = self._spawn(kind, job_id)
        except Exception:  # noqa: BLE001 - a job that can't even start must not stop the loop
            code, rc, stderr = "internal", None, traceback.format_exc().encode()
        finally:
            with self._lock:
                self._in_flight.discard(job_id)
        if code is None:
            row = store.get_job(job_id)
            if row is not None and row["state"] == "running":
                # A zero exit that left the row running is a task bug, not a success.
                code = "internal"
            else:
                log.info("job %s %s finished", log_id(job_id), kind)
                return None
        log.warning(
            "job %s %s failed: %s (exit %s) stderr: %s", log_id(job_id), kind, code, rc, loggable_tail(stderr)
        )
        store.transition(job_id, "running", "failed", error_code=code, message=MESSAGES[code])
        return code

    def _spawn(self, kind: str, job_id: str) -> tuple[str | None, int | None, bytes]:
        timeout = self.timeout(kind)
        cmd = sandbox.command(sandbox.limits(timeout), self.task(kind, job_id))
        env = {**os.environ, "PDFSPLIT_JOBS_DIR": str(self.settings.jobs_dir)}
        # A session of its own, so a timeout kills everything the task started, not just its pid.
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            start_new_session=True,
        )
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            _, err = proc.communicate()
            return "timeout", proc.returncode, err
        return classify(proc.returncode, out), proc.returncode, err

    # ── restart recovery ────────────────────────────────────────────────────────────────────────────────

    def recover(self, store: Store, now: datetime | None = None) -> None:
        """Jobs `running` for longer than their timeout lost their worker: re-queue each once, fail it the
        second time. The schema has no attempts column, so the first re-queue leaves `error_code='requeued'`
        on the row (claiming keeps it; `review`/`failed` overwrite it). This worker's own in-flight jobs are
        skipped, so the sweep can also run periodically, catching jobs orphaned by a quick restart."""
        now = now or datetime.now(UTC)
        with self._lock:
            mine = set(self._in_flight)
        for kind in self.kinds:
            for job in store.running_since(kind, now - timedelta(seconds=self.timeout(kind))):
                if job["id"] in mine:
                    continue
                if job["error_code"] == REQUEUED:
                    store.transition(job["id"], "running", "failed", error_code="timeout",
                                     message=MESSAGES["timeout"], now=now)
                else:
                    store.transition(job["id"], "running", "queued", error_code=REQUEUED, now=now)

    def _maybe_sweep(self, store: Store) -> None:
        with self._lock:
            if time.monotonic() - self._last_sweep < SWEEP_EVERY_S:
                return
            self._last_sweep = time.monotonic()
        self.recover(store)

    # ── the loop ────────────────────────────────────────────────────────────────────────────────────────

    def run_once(self, store: Store) -> bool:
        """Claim and run at most one job; False when the queue is empty."""
        for kind in self.kinds:
            job = store.claim_next(kind)
            if job is not None:
                self.execute(store, job)
                return True
        return False

    def _loop(self, stop: threading.Event) -> None:
        store = Store(self.settings.db_path)  # one Store per thread
        try:
            while not stop.is_set():
                try:
                    self._maybe_sweep(store)
                    busy = self.run_once(store)
                except Exception:  # noqa: BLE001 - e.g. sqlite busy past its timeout; retry after a pause
                    # Not log.exception: a traceback can quote an id-bearing path, so it is redacted first.
                    log.error("worker loop error: %s", loggable_tail(traceback.format_exc().encode()))
                    busy = False
                if not busy:
                    stop.wait(self.poll)
        finally:
            store.close()

    def serve(self, stop: threading.Event) -> None:
        """Run `settings.workers` claim loops until `stop` is set; each finishes its current job first."""
        self.settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        store = Store(self.settings.db_path)
        try:
            store.init()
            self.recover(store)
        finally:
            store.close()
        self._last_sweep = time.monotonic()
        threads = [
            threading.Thread(target=self._loop, args=(stop,), name=f"worker-{i}", daemon=True)
            for i in range(max(1, self.settings.workers))
        ]
        for t in threads:
            t.start()
        log.info("worker started: %d slot(s), kinds %s", len(threads), ",".join(self.kinds))
        for t in threads:
            t.join()


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
