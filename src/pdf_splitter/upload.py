"""`POST /api/jobs`: stream the upload to disk under a size cap, preflight it, then queue an analyze job."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse

from .config import Settings
from .deps import SettingsDep, StoreDep
from .preflight import MAGIC
from .store import Store, log_id, new_job_id
from .worker import sandbox

CHUNK = 1024 * 1024
PREFLIGHT_TIMEOUT = 10.0
MAX_FILENAME = 120
DEFAULT_FILENAME = "document.pdf"

MESSAGES = {
    "too_large": "The file is larger than the upload limit.",
    "not_pdf": "The file is not a PDF.",
    "encrypted": "The PDF is password-protected.",
    "too_many_pages": "The PDF has more pages than the limit.",
    "no_text_layer": "The PDF has no text layer (it looks scanned); run OCR on it first.",
    "unreadable": "The PDF could not be read.",
}
STATUS = {"too_large": 413, "too_many_pages": 413}

log = logging.getLogger(__name__)
router = APIRouter()

# Per-process salt: rows need *some* NOT NULL ip_hash, and the raw address must never be stored.
# STORY-012 replaces this with the daily-rotating salt and the trusted-proxy rule.
_IP_SALT = secrets.token_bytes(16)


def ip_hash(host: str | None) -> str:
    return hashlib.sha256(_IP_SALT + (host or "unknown").encode()).hexdigest()


def sanitize_filename(name: str | None) -> str:
    """A display name only; files on disk are always `<id>/source.pdf`, never this."""
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if unicodedata.category(c)[0] != "C").strip()
    if name in ("", ".", ".."):
        return DEFAULT_FILENAME
    if len(name) > MAX_FILENAME:
        stem, dot, ext = name.rpartition(".")
        # Keep a short extension so the truncated name still reads as a PDF in the UI.
        if dot and stem and len(ext) <= 8:
            name = stem[: MAX_FILENAME - len(ext) - 1].rstrip() + "." + ext
        else:
            name = name[:MAX_FILENAME]
    return name


def reject(code: str) -> JSONResponse:
    return JSONResponse({"code": code, "message": MESSAGES[code]}, status_code=STATUS.get(code, 400))


def run_preflight(path: Path, max_pages: int, job_id: str, timeout: float | None = None) -> dict[str, Any]:
    """Run the preflight subprocess; a crash, timeout or garbage output is `unreadable`."""
    timeout = timeout or PREFLIGHT_TIMEOUT
    cpu_limit = sandbox.limits(timeout)["cpu"]
    # `--` ends option parsing: a job id can start with `-`, and the path must never read as an option.
    cmd = [
        sys.executable, "-m", "pdf_splitter.preflight",
        "--max-pages", str(max_pages), "--cpu-limit", str(cpu_limit), "--", str(path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL
        )
    except subprocess.TimeoutExpired:
        log.warning("job %s preflight timed out", log_id(job_id))
        return {"ok": False, "code": "unreadable"}
    try:
        # Only the last line: PyMuPDF prints its own notices (e.g. the `fitz` deprecation) to stdout.
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        ok = proc.returncode == 0 and isinstance(result, dict) and "ok" in result
    except (ValueError, IndexError):
        ok = False
    if not ok:
        # The stderr tail can name the file's path, which contains the job id; log only the exit code.
        log.warning("job %s preflight failed (exit %s)", log_id(job_id), proc.returncode)
        return {"ok": False, "code": "unreadable"}
    if result["ok"] is True and not (isinstance(result.get("pages"), int) and result["pages"] > 0):
        return {"ok": False, "code": "unreadable"}
    if result["ok"] is not True and result.get("code") not in MESSAGES:
        return {"ok": False, "code": "unreadable"}
    return result


def _copy_capped(file: UploadFile, dest: Path, max_bytes: int) -> int | None:
    """Copy in chunks; None once the count passes `max_bytes`, so no more than one chunk is ever held."""
    total = 0
    with dest.open("wb") as out:
        while chunk := file.file.read(CHUNK):
            total += len(chunk)
            if total > max_bytes:
                return None
            out.write(chunk)
    return total


def _accept(file: UploadFile | None, request: Request, settings: Settings, store: Store) -> JSONResponse:
    if file is None:
        return reject("not_pdf")
    job_id = new_job_id()
    job_dir = settings.jobs_dir / job_id
    part = job_dir / "source.pdf.part"
    # mkdir outside the try: if it ever collided, the cleanup must not delete another job's directory.
    job_dir.mkdir()
    created = False
    try:
        size = _copy_capped(file, part, settings.max_bytes)
        if size is None:
            return reject("too_large")
        with part.open("rb") as f:
            if f.read(len(MAGIC)) != MAGIC:
                return reject("not_pdf")
        result = run_preflight(part, settings.max_pages, job_id)
        if not result["ok"]:
            log.info("job %s rejected: %s", log_id(job_id), result["code"])
            return reject(result["code"])
        part.rename(job_dir / "source.pdf")
        job = store.create_job(
            job_id=job_id,
            ip_hash=ip_hash(request.client.host if request.client else None),
            filename=sanitize_filename(file.filename),
            bytes=size,
            pages=result["pages"],
            ttl_hours=settings.ttl_hours,
        )
        created = True
        return JSONResponse({"id": job["id"], "state": job["state"]}, status_code=201)
    finally:
        if not created:
            shutil.rmtree(job_dir, ignore_errors=True)


@router.post("/api/jobs", status_code=201)
def create_job(
    request: Request, settings: SettingsDep, store: StoreDep, file: UploadFile | None = None
) -> JSONResponse:
    # A sync route: the chunked copy, the preflight subprocess and sqlite all block, so they run on
    # the threadpool rather than the event loop.
    return _accept(file, request, settings, store)
