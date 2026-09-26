"""What every job route does first: find the row, refuse the gone and the not-yet, build the job's paths."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import ValidationError

from ..config import Settings
from ..errors import ApiError, field_errors
from ..store import Store, now_ts

EDITABLE = ("review", "done")      # the states a Plan may change in, and a cut may start from; plus a failed cut
BUSY = ("queued", "running")


def gone(job: dict[str, Any]) -> bool:
    """Deleted, or past its TTL and waiting for the janitor: 410 on every job route."""
    return job["state"] == "deleted" or job["expires_at"] < now_ts()


def load_job(store: Store, job_id: str) -> dict[str, Any]:
    """The row, or 404 (unknown) / 410 (`gone`)."""
    job = store.get_job(job_id)
    if job is None:
        raise ApiError(404, "not_found")
    if gone(job):
        raise ApiError(410, "expired")
    return job


def job_dir(settings: Settings, job: dict[str, Any]) -> Path:
    # Only an id read back from the table ever becomes a path component.
    return settings.jobs_dir / job["id"]


def analyzed(job: dict[str, Any]) -> bool:
    """The analysis and plan exist: from `review` on, including a queued, running or failed cut."""
    return job["state"] in EDITABLE or job["kind"] == "cut"


def require_analyzed(job: dict[str, Any]) -> None:
    if not analyzed(job):
        raise ApiError(409, "not_ready")


def editable(job: dict[str, Any]) -> bool:
    """Architecture § Job states: a failed CUT keeps its analysis and plan, so it is edited and re-cut like
    `review`; a failed analyze has nothing to edit and stays terminal."""
    return job["state"] in EDITABLE or (job["state"] == "failed" and job["kind"] == "cut")


def require_editable(job: dict[str, Any]) -> None:
    if not editable(job):
        raise ApiError(409, "busy" if job["state"] in BUSY else "not_ready")


def read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def vanished(store: Store, settings: Settings, job: dict[str, Any]) -> ApiError:
    """A job file that is not there: a DELETE (or the janitor) landed after `load_job` — the row says so, and
    whatever a late write brought back goes with it — is the 410 that DELETE earned; otherwise the job is simply
    not that far yet."""
    row = store.get_job(job["id"])
    if row is not None and row["state"] == "deleted":
        shutil.rmtree(job_dir(settings, job), ignore_errors=True)
    if row is None or gone(row):
        return ApiError(410, "expired")
    return ApiError(409, "not_ready")


def job_file(store: Store, settings: Settings, job: dict[str, Any], name: str) -> bytes:
    """One of the job's JSON files, read — not `exists()` then read, which a DELETE in between turned into a 500."""
    try:
        return read_bytes(job_dir(settings, job) / name)
    except FileNotFoundError:
        raise vanished(store, settings, job) from None


def saved_plan(store: Store, settings: Settings, job: dict[str, Any]) -> dict[str, Any]:
    require_analyzed(job)
    return json.loads(job_file(store, settings, job, "plan.json").decode("utf-8"))


def invalid(e: ValidationError, where: str = "body") -> ApiError:
    return ApiError(422, "invalid", errors=[{**err, "loc": [where, *err["loc"]]} for err in field_errors(e.errors())])


def invalid_field(loc: list[str | int], msg: str, type_: str = "value_error") -> ApiError:
    """A single field error in the same shape as the Pydantic ones."""
    return ApiError(422, "invalid", errors=[{"loc": loc, "msg": msg, "type": type_}])


def attachment(filename: str) -> str:
    """A Content-Disposition like Starlette's FileResponse builds: RFC 5987 only when the name needs it."""
    quoted = quote(filename)
    return f"attachment; filename*=utf-8''{quoted}" if quoted != filename else f'attachment; filename="{filename}"'
