"""Sheet PNGs and Section plans, each rendered by a sandboxed `pdf_splitter.preview` subprocess (20 s)."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Response
from monograph_splitter.profile import profile_from_dict

from ..access_log import loggable_tail
from ..config import Settings
from ..deps import SettingsDep, StoreDep
from ..errors import ApiError
from ..files import read_json
from ..models import PreviewRequest, ValidationError
from ..preview import GONE
from ..store import Store, log_id
from ..worker import sandbox
from .common import gone, invalid, invalid_field, job_dir, load_job, saved_plan

log = logging.getLogger(__name__)
router = APIRouter()

PREVIEW_TIMEOUT = 20.0
DPIS = (48, 72, 110)


def run_preview(kind: str, job_dir: Path, request: dict[str, Any], timeout: float | None = None) -> dict | None:
    """Run one preview subprocess under the worker's rlimits; None on any failure (logged, redacted)."""
    timeout = timeout or PREVIEW_TIMEOUT
    # `--` ends option parsing: the job dir is absolute, but its id component can start with `-`.
    cmd = sandbox.command(sandbox.limits(timeout), ["-m", "pdf_splitter.preview", kind, "--", str(job_dir)])
    lid = log_id(job_dir.name)
    try:
        proc = subprocess.run(
            cmd, input=json.dumps(request).encode(), capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        log.warning("job %s preview %s timed out", lid, kind)
        return None
    try:
        # Only the last line: PyMuPDF prints its own notices (e.g. the `fitz` deprecation) to stdout.
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        result = None
    if isinstance(result, dict) and result.get("ok") is True and proc.returncode == 0:
        return result
    if isinstance(result, dict) and result.get("code") == GONE:
        # A DELETE raced with the render; the caller's `settled` turns it into the 410.
        log.info("job %s preview %s found the job gone", lid, kind)
        return None
    log.warning(
        "job %s preview %s failed (exit %s) stderr: %s", lid, kind, proc.returncode, loggable_tail(proc.stderr)
    )
    return None


def settled(store: Store, settings: Settings, job: dict[str, Any]) -> None:
    """The row again once the subprocess is back: a DELETE may have landed while it rendered, and anything it
    wrote after the directory went (the engine's `mkdir(parents=True)` brings `work/` back) goes with the job.
    Raises the 410 the DELETE would have earned before the render."""
    row = store.get_job(job["id"])
    if row is not None and row["state"] == "deleted":
        shutil.rmtree(job_dir(settings, job), ignore_errors=True)
    if row is None or gone(row):
        raise ApiError(410, "expired")


def _settings_hash(settings: dict[str, Any]) -> str:
    return profile_from_dict(settings).sha256[:12]


def _cached(path: Path) -> bytes | None:
    # Read, not `exists()` then FileResponse: a file a DELETE removes between the two would be a 500 from
    # inside Starlette; a vanished file is simply a cache miss.
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


@router.get("/api/jobs/{job_id}/sheets/{n}.png")
def get_sheet(job_id: str, n: int, settings: SettingsDep, store: StoreDep, dpi: int = 72) -> Response:
    if dpi not in DPIS:
        raise invalid_field(["query", "dpi"], f"dpi must be one of {', '.join(map(str, DPIS))}", "literal_error")
    job = load_job(store, job_id)
    plan = saved_plan(settings, job)
    if not 1 <= n <= job["pages"]:
        raise ApiError(404, "not_found", "There is no page with that number.")
    # Architecture § File layout: `png/<dpi>/<sheet>-<hash>.png`, one render per (sheet, dpi, settings).
    cache = job_dir(settings, job) / "png" / str(dpi) / f"{n}-{_settings_hash(plan['settings'])}.png"
    data = _cached(cache)
    if data is None:
        request = {"sheet": n, "dpi": dpi, "out": str(cache)}
        result = run_preview("sheet", job_dir(settings, job), request)
        settled(store, settings, job)
        data = _cached(cache) if result is not None else None
        if data is None:
            raise ApiError(500, "preview_failed")
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.post("/api/jobs/{job_id}/sections/{i}/plan")
def post_section_plan(
    job_id: str, i: int, settings: SettingsDep, store: StoreDep, body: Annotated[dict[str, Any] | None, Body()] = None
) -> dict[str, Any]:
    job = load_job(store, job_id)
    plan = saved_plan(settings, job)
    if not 0 <= i < len(plan["sections"]):
        raise ApiError(404, "not_found", "There is no section with that number.")
    try:
        heights = [float(s["H"]) for s in read_json(job_dir(settings, job) / "analysis.json")["size"]]
    except FileNotFoundError:
        settled(store, settings, job)         # the directory went with a DELETE just now → 410
        raise
    context = {"height": heights[plan["sections"][i]["page"] - 1], "max_height": max(heights)}
    try:
        req = PreviewRequest.model_validate(body or {}, context=context)
    except ValidationError as e:
        raise invalid(e) from None
    override = req.override.dump() if req.override is not None else None
    if "override" not in req.model_fields_set:
        override = plan["overrides"].get(str(i))
    request = {
        "sections": plan["sections"],
        "settings": req.settings.model_dump() if req.settings is not None else plan["settings"],
        "index": i,
        "override": override,
    }
    result = run_preview("section", job_dir(settings, job), request)
    settled(store, settings, job)
    if result is None:
        raise ApiError(500, "preview_failed")
    return result["plan"]
