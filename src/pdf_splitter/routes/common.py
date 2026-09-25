"""What every job route does first: find the row, refuse the gone and the not-yet, build the job's paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import ValidationError

from ..config import Settings
from ..errors import ApiError, field_errors
from ..files import read_json
from ..store import Store, now_ts

EDITABLE = ("review", "done")      # the states a Plan may change in, and a cut may start from
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


def require_editable(job: dict[str, Any]) -> None:
    if job["state"] not in EDITABLE:
        raise ApiError(409, "busy" if job["state"] in BUSY else "not_ready")


def saved_plan(settings: Settings, job: dict[str, Any]) -> dict[str, Any]:
    require_analyzed(job)
    path = job_dir(settings, job) / "plan.json"
    if not path.exists():
        raise ApiError(409, "not_ready")
    return read_json(path)


def invalid(e: ValidationError, where: str = "body") -> ApiError:
    return ApiError(422, "invalid", errors=[{**err, "loc": [where, *err["loc"]]} for err in field_errors(e.errors())])


def invalid_field(loc: list[str | int], msg: str, type_: str = "value_error") -> ApiError:
    """A single field error in the same shape as the Pydantic ones."""
    return ApiError(422, "invalid", errors=[{"loc": loc, "msg": msg, "type": type_}])


def attachment(filename: str) -> str:
    """A Content-Disposition like Starlette's FileResponse builds: RFC 5987 only when the name needs it."""
    quoted = quote(filename)
    return f"attachment; filename*=utf-8''{quoted}" if quoted != filename else f'attachment; filename="{filename}"'
