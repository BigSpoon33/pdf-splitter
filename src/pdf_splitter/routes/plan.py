"""Job status, the analysis, the Plan (GET/PUT) and queueing a cut."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body
from fastapi.responses import FileResponse, JSONResponse

from ..deps import SettingsDep, StoreDep
from ..errors import ApiError
from ..files import read_json, write_json
from ..models import ValidationError, validate_plan
from .common import (
    BUSY,
    EDITABLE,
    invalid,
    invalid_field,
    job_dir,
    load_job,
    require_analyzed,
    require_editable,
    saved_plan,
)

router = APIRouter()


def seconds_left(job: dict[str, Any], now: datetime | None = None) -> int:
    """Time until the janitor's deadline, by the server's clock: the SPA counts down from this, since a visitor's
    clock can be hours off `expires_at` and only a 410 from here may declare a job gone."""
    remaining = datetime.fromisoformat(job["expires_at"]) - (now or datetime.now(UTC))
    return max(0, int(remaining.total_seconds()))


def status_of(job: dict[str, Any]) -> dict[str, Any]:
    """The polling shape (Architecture § API Interface). `error_code` only with `failed`: a queued or running
    row can carry the worker's re-queue marker, which is not an error. `queue_position` is STORY-012's."""
    return {
        "id": job["id"],
        "state": job["state"],
        "kind": job["kind"],
        "progress": job["progress"],
        "total": job["total"],
        "queue_position": None,
        "message": job["message"],
        "error_code": job["error_code"] if job["state"] == "failed" else None,
        "expires_at": job["expires_at"],
        "seconds_left": seconds_left(job),
        "filename": job["filename"],
        "pages": job["pages"],
    }


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str, store: StoreDep) -> dict[str, Any]:
    return status_of(load_job(store, job_id))


@router.get("/api/jobs/{job_id}/analysis")
def get_analysis(job_id: str, settings: SettingsDep, store: StoreDep) -> FileResponse:
    job = load_job(store, job_id)
    require_analyzed(job)
    path = job_dir(settings, job) / "analysis.json"
    if not path.exists():
        raise ApiError(409, "not_ready")
    # The file as the worker wrote it (already UTF-8-safe), without re-encoding a large document.
    return FileResponse(path, media_type="application/json")


@router.get("/api/jobs/{job_id}/plan")
def get_plan(job_id: str, settings: SettingsDep, store: StoreDep) -> FileResponse:
    job = load_job(store, job_id)
    saved_plan(settings, job)
    return FileResponse(job_dir(settings, job) / "plan.json", media_type="application/json")


@router.put("/api/jobs/{job_id}/plan")
def put_plan(
    job_id: str, settings: SettingsDep, store: StoreDep, body: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    job = load_job(store, job_id)
    require_editable(job)
    analysis = read_json(job_dir(settings, job) / "analysis.json")
    try:
        plan = validate_plan(body, pages=job["pages"], sizes=analysis["size"])
    except ValidationError as e:
        raise invalid(e) from None
    data = plan.dump()
    write_json(job_dir(settings, job) / "plan.json", data)
    if job["state"] == "done":
        # The saved outputs no longer match the plan; they stay downloadable until the next cut replaces them.
        store.transition(job_id, "done", "review")
    return data


@router.post("/api/jobs/{job_id}/cut", status_code=202)
def post_cut(job_id: str, settings: SettingsDep, store: StoreDep) -> JSONResponse:
    job = load_job(store, job_id)
    require_editable(job)
    if not saved_plan(settings, job)["sections"]:
        raise invalid_field(["plan", "sections"], "The plan has no sections.")
    # Conditional, so a second click or a claim in between can't queue the job twice or reset a running cut.
    if not store.transition(job_id, EDITABLE, "queued", kind="cut"):
        current = store.get_job(job_id)
        raise ApiError(409, "busy" if current and current["state"] in BUSY else "not_ready")
    return JSONResponse({"id": job_id, "state": "queued"}, status_code=202)
