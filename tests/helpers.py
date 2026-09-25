"""Helpers shared by the worker, cut and API tests."""

from __future__ import annotations

import shutil
from pathlib import Path

from pdf_splitter.config import Settings
from pdf_splitter.store import Store

DASH_ID = "-bCdEfGhIjKlMnOpQrSt00"  # the token_urlsafe(16) shape with the awkward leading `-` (1 in 64)
OTHER_ID = "xYzAbCdEfGhIjKlMnOpQ01"


def assert_id_gone(out: str, job_id: str) -> None:
    """Neither the id nor any 16-char window of it survives (tests/test_upload.py's rule)."""
    assert job_id not in out
    assert not any(job_id[i : i + 16] in out for i in range(len(job_id) - 15))


def seed_job(
    settings: Settings, template: Path, job_id: str, *, state: str = "review", kind: str = "analyze",
    filename: str = "My Book.pdf",
) -> Path:
    """A job directory copied from the analyzed template (see conftest) plus its row, in any state."""
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    job_dir = settings.jobs_dir / job_id
    shutil.copytree(template, job_dir)
    store = Store(settings.db_path)
    try:
        store.init()
        store.create_job(
            job_id=job_id, ip_hash="h", filename=filename, bytes=(job_dir / "source.pdf").stat().st_size,
            pages=6, ttl_hours=24, state=state, kind=kind,
        )
    finally:
        store.close()
    return job_dir


def row(settings: Settings, job_id: str) -> dict:
    store = Store(settings.db_path)
    try:
        job = store.get_job(job_id)
    finally:
        store.close()
    assert job is not None
    return job
