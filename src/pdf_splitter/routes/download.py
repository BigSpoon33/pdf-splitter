"""The outputs: `result.zip`, one section's PDF out of it, and deleting the job."""

from __future__ import annotations

import json
import shutil
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Response
from fastapi.responses import FileResponse, StreamingResponse

from ..deps import SettingsDep, StoreDep
from ..errors import ApiError
from ..worker.cut import MANIFEST
from .common import attachment, job_dir, load_job

router = APIRouter()

CHUNK = 256 * 1024
MAX_STEM = 80


def download_name(filename: str) -> str:
    return f"{Path(filename).stem[:MAX_STEM].strip() or 'document'}-sections.zip"


def _result_zip(settings: SettingsDep, job: dict[str, Any]) -> Path:
    path = job_dir(settings, job) / "result.zip"
    if not path.exists():
        raise ApiError(409, "not_ready")
    return path


@router.get("/api/jobs/{job_id}/result.zip")
def get_result(job_id: str, settings: SettingsDep, store: StoreDep) -> FileResponse:
    job = load_job(store, job_id)
    return FileResponse(_result_zip(settings, job), media_type="application/zip", filename=download_name(job["filename"]))


@router.get("/api/jobs/{job_id}/manifest")
def get_manifest(job_id: str, settings: SettingsDep, store: StoreDep) -> list[dict[str, Any]]:
    """The last cut's rows (each with its plan `index`), read from the same ZIP the downloads come from, so
    the flags the SPA badges are the flags of the files it can download."""
    job = load_job(store, job_id)
    with zipfile.ZipFile(_result_zip(settings, job)) as zf:
        return json.loads(zf.read(MANIFEST))


def _member(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> Iterator[bytes]:
    try:
        with zf.open(info) as f:
            while chunk := f.read(CHUNK):
                yield chunk
    finally:
        zf.close()


@router.get("/api/jobs/{job_id}/sections/{i}.pdf")
def get_section(job_id: str, i: int, settings: SettingsDep, store: StoreDep) -> StreamingResponse:
    job = load_job(store, job_id)
    # Served out of the ZIP, never from work/: the ZIP is the one artifact a cut replaces whole, so a section
    # and the archive it came from can't disagree, and a running re-cut can't take a download away.
    zf = zipfile.ZipFile(_result_zip(settings, job))
    try:
        row = next((r for r in json.loads(zf.read(MANIFEST)) if r["index"] == i), None)
        if row is None:
            raise ApiError(404, "not_found", "There is no section with that number.")
        info = zf.getinfo(row["file"])
    except BaseException:
        zf.close()
        raise
    headers = {"Content-Length": str(info.file_size), "Content-Disposition": attachment(row["file"])}
    return StreamingResponse(_member(zf, info), media_type="application/pdf", headers=headers)


@router.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str, settings: SettingsDep, store: StoreDep) -> Response:
    job = store.get_job(job_id)
    if job is None:
        raise ApiError(404, "not_found")
    # The row first: a task still running on this job then finds it not `running` and writes nothing new.
    store.set_state(job_id, "deleted")
    shutil.rmtree(job_dir(settings, job), ignore_errors=True)
    return Response(status_code=204)
