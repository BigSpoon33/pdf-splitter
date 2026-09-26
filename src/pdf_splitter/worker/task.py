"""One job in its own sandboxed process: `python -m pdf_splitter.task analyze|cut -- <id>`.

The runner starts it through `worker.sandbox` (rlimits) under a wall timeout and passes `PDFSPLIT_JOBS_DIR`.
The task writes its outputs and moves the row `running → review` (`→ done` for a cut) itself; any failure is
left to the runner,
which reads the result from the LAST stdout line (the engine's lazy `import fitz` prints a deprecation notice
to stdout mid-run): `{"ok": true}` or `{"ok": false, "code": "resources"|"too_large_output"|"internal"}`.
"""

from __future__ import annotations

import argparse
import errno
import json
import re
import sys
import time
import traceback
from collections.abc import Callable

import pymupdf

from ..config import Settings
from ..files import read_json, read_mode, write_json
from ..store import Store
from .analyze import MUPDF_ERRORS, analyze, default_plan, ranges_plan
from .cut import MSG_PACKAGING, OutputTooLarge, cut_book, cut_ranges, output_budget, package

PROGRESS_INTERVAL_S = 0.5
EXIT_INTERNAL = 1
EXIT_RESOURCES = 3
# MuPDF allocates outside Python, so under RLIMIT_AS its allocator failing is not a MemoryError but
# FZ_ERROR_SYSTEM (`code=2: calloc (4104 x 1 bytes) failed`). PyMuPDF 1.28 surfaces it as a plain
# RuntimeError from its `_extra` helpers and as `pymupdf.mupdf.FzErrorSystem` from the raw bindings, so the
# exception TYPE says nothing about the cause: only the message does.
MUPDF_ALLOC_FAILED = re.compile(
    r"^\s*code=2\b|\b(?:calloc|malloc|realloc)\b.*\bfailed\b|\bout of memory\b", re.IGNORECASE
)


def is_mupdf_alloc_failure(e: BaseException) -> bool:
    """A MuPDF error (either raise path) whose message is the allocator's; a `ValueError` or an `OSError`
    carrying the same words is not, because those come from Python code, not from MuPDF's allocator."""
    return isinstance(e, MUPDF_ERRORS) and MUPDF_ALLOC_FAILED.search(str(e)) is not None


class Throttle:
    """Progress writes at most every `interval` seconds (AC-3). Page ticks inside the window are dropped;
    a message change (a new phase) is never dropped and waits out the window instead, so the row always
    ends up showing the phase the job is in."""

    def __init__(
        self,
        write: Callable[[int, int, str], None],
        interval: float = PROGRESS_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._write, self._interval, self._clock, self._sleep = write, interval, clock, sleep
        self._last: float | None = None
        self._message: str | None = None

    def __call__(self, done: int, total: int, message: str) -> None:
        now = self._clock()
        if self._last is not None and now - self._last < self._interval:
            if message == self._message:
                return
            self._sleep(self._interval - (now - self._last))
            now = self._clock()
        self._write(done, total, message)
        self._last, self._message = now, message


def run_analyze(settings: Settings, job_id: str, store: Store) -> bool:
    """Analyze one job; False if its row is gone or no longer running (nothing is written then)."""
    job = store.get_job(job_id)
    if job is None or job["state"] != "running" or job["kind"] != "analyze":
        return False
    job_dir = settings.jobs_dir / job_id
    throttle = Throttle(lambda done, total, msg: store.update_progress(job_id, done, total, msg))
    analysis = analyze(job_dir / "source.pdf", job_dir / "work", throttle)
    write_json(job_dir / "analysis.json", analysis)
    # The mode was fixed at upload (ADR-009 as built): a page-range job starts from an empty span list, never from
    # the chapter suggestion — so no link, query or later load ever has a chapter plan to convert.
    write_json(job_dir / "plan.json", ranges_plan() if read_mode(job_dir) == "ranges" else default_plan(analysis))
    return store.transition(job_id, "running", "review")


def run_cut(settings: Settings, job_id: str, store: Store) -> bool:
    """Cut one job from its saved plan into `result.zip`; False if its row is gone or no longer running."""
    job = store.get_job(job_id)
    if job is None or job["state"] != "running" or job["kind"] != "cut":
        return False
    job_dir = settings.jobs_dir / job_id
    throttle = Throttle(lambda done, total, msg: store.update_progress(job_id, done, total, msg))
    plan = read_json(job_dir / "plan.json")
    limit = output_budget(settings.max_output_bytes, job["bytes"])
    # ADR-009: a page-range plan is whole-page copies, not an engine cut.
    rows, _ = (cut_ranges if plan["source"] == "ranges" else cut_book)(job_dir, plan, throttle, limit)
    # A job deleted while the engine ran must not get its directory back: the check sits right before the
    # only write the api serves (the engine's work files are already on disk and go with the row's TTL).
    row = store.get_job(job_id)
    if row is None or row["state"] != "running":
        return False
    throttle(len(rows), len(rows), MSG_PACKAGING)
    package(job_dir, rows)
    return store.transition(job_id, "running", "done")


def _result(ok: bool, code: str | None = None) -> int:
    print(json.dumps({"ok": True} if ok else {"ok": False, "code": code}), flush=True)
    return 0 if ok else (EXIT_RESOURCES if code == "resources" else EXIT_INTERNAL)


def guarded(job: Callable[[], bool]) -> int:
    """Run `job` and print its result line; the exit code the runner sees. Every kind of task (and the
    tests' fake tasks) goes through this, so a hit rlimit reads as `resources` the same way everywhere."""
    try:
        ok = job()
    except MemoryError:
        return _result(False, "resources")
    except OutputTooLarge:
        return _result(False, "too_large_output")
    except OSError as e:
        # RLIMIT_FSIZE: Python ignores SIGXFSZ, so the oversized write fails with EFBIG instead.
        if e.errno == errno.EFBIG:
            return _result(False, "resources")
        traceback.print_exc()
        return _result(False, "internal")
    except Exception as e:  # noqa: BLE001 - any other failure is `internal`; the runner logs the redacted tail
        if is_mupdf_alloc_failure(e):
            return _result(False, "resources")
        traceback.print_exc()
        return _result(False, "internal")
    return _result(True) if ok else _result(False, "internal")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pdf_splitter.task")
    parser.add_argument("kind", choices=["analyze", "cut"])
    parser.add_argument("job_id")
    args = parser.parse_args(argv)
    # The id comes from the jobs table, but it is also a path component: refuse anything that isn't one.
    if args.job_id in ("", ".", "..") or "/" in args.job_id or "\0" in args.job_id:
        return _result(False, "internal")
    # MuPDF's warnings on hostile files can quote file content; nothing downstream needs them.
    pymupdf.TOOLS.mupdf_display_errors(False)
    settings = Settings()
    store = Store(settings.db_path)
    run = run_analyze if args.kind == "analyze" else run_cut
    try:
        return guarded(lambda: run(settings, args.job_id, store))
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
