"""One job in its own sandboxed process: `python -m pdf_splitter.task analyze -- <id>`.

The runner starts it through `worker.sandbox` (rlimits) under a wall timeout and passes `PDFSPLIT_JOBS_DIR`.
The task writes its outputs and moves the row `running → review` itself; any failure is left to the runner,
which reads the result from the LAST stdout line (the engine's lazy `import fitz` prints a deprecation notice
to stdout mid-run): `{"ok": true}` or `{"ok": false, "code": "resources"|"internal"}`.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pymupdf

from ..config import Settings
from ..store import Store
from .analyze import analyze, default_plan

PROGRESS_INTERVAL_S = 0.5
EXIT_INTERNAL = 1
EXIT_RESOURCES = 3


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


def _write_json(path: Path, data: dict[str, Any]) -> None:
    # Write-then-rename: the api may read these files while the task runs, and must never see half of one.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)


def run_analyze(settings: Settings, job_id: str, store: Store) -> bool:
    """Analyze one job; False if its row is gone or no longer running (nothing is written then)."""
    job = store.get_job(job_id)
    if job is None or job["state"] != "running" or job["kind"] != "analyze":
        return False
    job_dir = settings.jobs_dir / job_id
    throttle = Throttle(lambda done, total, msg: store.update_progress(job_id, done, total, msg))
    analysis = analyze(job_dir / "source.pdf", job_dir / "work", throttle)
    _write_json(job_dir / "analysis.json", analysis)
    _write_json(job_dir / "plan.json", default_plan(analysis))
    return store.transition(job_id, "running", "review")


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
    except OSError as e:
        # RLIMIT_FSIZE: Python ignores SIGXFSZ, so the oversized write fails with EFBIG instead.
        if e.errno == errno.EFBIG:
            return _result(False, "resources")
        traceback.print_exc()
        return _result(False, "internal")
    except Exception:  # noqa: BLE001 - any other failure is `internal`; the runner logs the redacted tail
        traceback.print_exc()
        return _result(False, "internal")
    return _result(True) if ok else _result(False, "internal")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pdf_splitter.task")
    parser.add_argument("kind", choices=["analyze"])
    parser.add_argument("job_id")
    args = parser.parse_args(argv)
    # The id comes from the jobs table, but it is also a path component: refuse anything that isn't one.
    if args.job_id in ("", ".", "..") or "/" in args.job_id or "\0" in args.job_id:
        return _result(False, "internal")
    # MuPDF's warnings on hostile files can quote file content; nothing downstream needs them.
    pymupdf.TOOLS.mupdf_display_errors(False)
    settings = Settings()
    store = Store(settings.db_path)
    try:
        return guarded(lambda: run_analyze(settings, args.job_id, store))
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
