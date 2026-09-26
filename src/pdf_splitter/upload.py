"""`POST /api/jobs`: refuse what can be refused from the headers alone (`UploadGuard`), stream the upload to disk
under a size cap, preflight it, then queue an analyze job."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from . import ratelimit
from .config import Settings
from .deps import SettingsDep, StoreDep
from .errors import MESSAGES
from .files import DEFAULT_MODE, write_mode
from .preflight import MAGIC
from .store import Store, log_id, new_job_id
from .worker import sandbox

CHUNK = 1024 * 1024
PREFLIGHT_TIMEOUT = 10.0
# ADR-009 as built: the split mode is a property of the job, chosen once here; FastAPI answers 422 for anything else.
Mode = Literal["chapters", "ranges"]
MAX_FILENAME = 120
DEFAULT_FILENAME = "document.pdf"
# What multipart wraps around the file — boundaries, the part headers (a long filename), the `mode` field: a
# Content-Length past MAX_BYTES by more than this cannot be a file under the cap. The streaming copy still
# decides the exact cap for anything that passes here.
ENVELOPE = 64 * 1024
UPLOAD_PATH = "/api/jobs"
MULTIPART = "multipart/form-data"
RETRY_AFTER_OVERLOADED = "5"

# The preflight's own codes; anything else it prints is `unreadable`.
PREFLIGHT_CODES = frozenset({"not_pdf", "encrypted", "too_many_pages", "no_text_layer", "unreadable"})
STATUS = {"too_large": 413, "too_many_pages": 413, "rate_limited": 429, "disk_full": 503, "overloaded": 503}

log = logging.getLogger(__name__)
router = APIRouter()


@contextmanager
def spooling_to(directory: Path) -> Iterator[None]:
    """Starlette spools every multipart part through `tempfile` (1 MiB in memory, then a temporary file), so while
    the app runs the process's temporary directory is this one on the jobs volume: in the container `/tmp` is a
    tmpfs the memory limit pays for, and a few big uploads in flight were enough to OOM-kill the api (gate r1).
    Children (the preflight, previews) inherit it through TMPDIR. Restored on shutdown so nothing outlives the app."""
    directory.mkdir(parents=True, exist_ok=True)
    previous, previous_env = tempfile.tempdir, os.environ.get("TMPDIR")
    tempfile.tempdir = os.environ["TMPDIR"] = str(directory)
    try:
        yield
    finally:
        tempfile.tempdir = previous
        if previous_env is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = previous_env


class UploadGuard:
    """Pure ASGI, around the app, for `POST /api/jobs` only: everything that can refuse an upload without reading
    it — the declared size, the in-flight cap, the client's rate slot, the disk guard — runs here, BEFORE the
    multipart parser has spooled a byte, so a flood of big bodies costs the api a header read each. A refusal
    closes the connection, or the client would keep sending a body nobody reads. The route gets the client's hash
    through `request.state.client_hash` and never claims a slot of its own."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app, self.settings = app, settings
        self.in_flight = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != UPLOAD_PATH:
            await self.app(scope, receive, send)
            return
        response = self.refuse_by_headers(Headers(scope=scope))
        if response is None and self.in_flight >= self.settings.max_uploads:
            response = refuse("overloaded", {"Retry-After": RETRY_AFTER_OVERLOADED})
        if response is not None:
            await response(scope, receive, send)
            return
        # Reserved before the first await: two requests must not both find room under the cap.
        self.in_flight += 1
        try:
            # sqlite and statvfs block; the event loop must stay free for the bodies already streaming.
            response = await run_in_threadpool(self.admit, Request(scope))
            if response is not None:
                await response(scope, receive, send)
                return
            await self.app(scope, receive, send)
        finally:
            self.in_flight -= 1

    def refuse_by_headers(self, headers: Headers) -> JSONResponse | None:
        declared = headers.get("content-length")
        if declared is None:
            # Chunked: the size can't be checked up front, and every real client sends one. No body at all has
            # nothing to spool; the route answers it (`not_pdf`).
            if "transfer-encoding" in headers:
                return refuse("invalid", status=411, message="Uploads need a Content-Length.")
            return None
        if not declared.isdigit():
            return refuse("invalid")
        if int(declared) > self.settings.max_bytes + ENVELOPE:
            return refuse("too_large")
        # Only multipart streams to a spool; the urlencoded parser would hold the whole body in memory.
        if int(declared) and not headers.get("content-type", "").lower().startswith(MULTIPART):
            return refuse("invalid", status=415, message="Uploads are multipart/form-data.")
        return None

    def admit(self, request: Request) -> JSONResponse | None:
        settings = self.settings
        # The window counts the attempt (a refused file still costs a copy and a preflight), and the slot is
        # claimed in the same transaction that counts the window, so parallel uploads from one client can't all
        # slip past a still-empty count. It comes before the disk guard, so a slot is spent on an attempt the
        # guard then refuses: a client retrying into a full disk burns its hour, which is the cheaper failure
        # than a guard that lets a burst through.
        store = Store(settings.db_path)
        try:
            client, wait = ratelimit.take_slot(
                store,
                ratelimit.client_ip(request, settings.trusted_proxy),
                settings.rate_per_hour,
                secret=settings.ip_salt,
            )
        finally:
            store.close()
        if wait is not None:
            log.info("upload refused: rate limited (%s, retry after %ds)", client[:8], wait)
            return refuse("rate_limited", {"Retry-After": str(wait)})
        if disk_full(settings):
            log.warning("upload refused: less than %s GB free", settings.min_free_gb)
            return refuse("disk_full")
        request.state.client_hash = client
        return None


def refuse(
    code: str, headers: dict[str, str] | None = None, *, status: int | None = None, message: str | None = None
) -> JSONResponse:
    """A guard rejection: the usual body, plus `Connection: close` so the unread body stops at the socket."""
    return JSONResponse(
        {"code": code, "message": message or MESSAGES[code]},
        status_code=status or STATUS.get(code, 400),
        headers={"Connection": "close", **(headers or {})},
    )


def disk_full(settings: Settings) -> bool:
    """The guard Architecture § janitor describes: no new upload while the jobs volume is nearly full. Decimal GB,
    the same unit `/api/health` reports."""
    return shutil.disk_usage(settings.jobs_dir).free < settings.min_free_gb * 1e9


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


def reject(code: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"code": code, "message": MESSAGES[code]}, status_code=STATUS.get(code, 400), headers=headers)


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
    if result["ok"] is not True and result.get("code") not in PREFLIGHT_CODES:
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


def _accept(
    file: UploadFile | None, mode: Mode, request: Request, settings: Settings, store: Store
) -> JSONResponse:
    if file is None:
        return reject("not_pdf")
    # The slot and the disk guard ran in UploadGuard before the body was read; claiming again here would charge
    # every upload twice. A refused upload still leaves no directory and no row.
    client: str = request.state.client_hash
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
        # Written before the row exists, so a queued job can never be analyzed without it; no later request can
        # change it — the review screen's mode comes from the plan this produces, never from a URL.
        if mode != DEFAULT_MODE:
            write_mode(job_dir, mode)
        job = store.create_job(
            job_id=job_id,
            ip_hash=client,
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
    request: Request,
    settings: SettingsDep,
    store: StoreDep,
    file: UploadFile | None = None,
    mode: Annotated[Mode, Form()] = DEFAULT_MODE,
) -> JSONResponse:
    # A sync route: the chunked copy, the preflight subprocess and sqlite all block, so they run on
    # the threadpool rather than the event loop.
    return _accept(file, mode, request, settings, store)
