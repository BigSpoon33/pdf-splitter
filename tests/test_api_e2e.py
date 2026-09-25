"""STORY-007: the job API (status, analysis, plan, previews, cut, downloads, delete, request ids) and the
end-to-end flow on the synthetic book. Every payload the SPA relies on is pinned here."""

from __future__ import annotations

import contextlib
import io
import json
import logging
import subprocess
import sys
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import fitz
import pymupdf
import pytest
from fastapi.testclient import TestClient
from fixtures.books import headed_book
from helpers import DASH_ID, OTHER_ID, assert_id_gone, row, seed_job
from monograph_splitter.profile import profile_from_dict
from monograph_splitter.session import OVERRIDES_NAME

from pdf_splitter import errors, preview
from pdf_splitter.app import create_app
from pdf_splitter.config import Settings
from pdf_splitter.files import read_json
from pdf_splitter.routes import download as download_routes
from pdf_splitter.routes import preview as preview_routes
from pdf_splitter.store import Store, log_id
from pdf_splitter.worker.analyze import DEFAULT_SETTINGS
from pdf_splitter.worker.cut import MANIFEST, zip_entry
from pdf_splitter.worker.runner import Runner

JSON = {"content-type": "application/json"}
STATUS_KEYS = {
    "id", "state", "kind", "progress", "total", "queue_position", "message", "error_code", "expires_at",
    "filename", "pages",
}
SECTION_PLAN_KEYS = {"pages", "startCut", "startCol", "endCut", "endCol", "flags", "notes", "rects"}


def api(settings: Settings) -> TestClient:
    return TestClient(create_app(settings), raise_server_exceptions=True)


def assert_error(r, status: int, code: str) -> None:
    assert r.status_code == status, r.text
    body = r.json()
    assert body["code"] == code and body["message"]
    assert set(body) - {"errors", "request_id"} == {"code", "message"}
    assert r.headers["x-request-id"]


def locs(r) -> list[list]:
    return [e["loc"] for e in r.json()["errors"]]


@pytest.fixture
def seeded(settings: Settings, analyzed_template: Path) -> Path:
    return seed_job(settings, analyzed_template, DASH_ID)


# ── AC-6: end to end, real subprocesses for both jobs ─────────────────────────────────────────────────


def test_end_to_end_upload_analyze_plan_cut_download(settings: Settings, tmp_path: Path) -> None:
    headed_book(tmp_path / "book.pdf", outline=False)
    with api(settings) as client:
        r = client.post("/api/jobs", files={"file": ("My Book.pdf", (tmp_path / "book.pdf").read_bytes(), "application/pdf")})
        assert r.status_code == 201, r.text
        job_id = r.json()["id"]
        store = Store(settings.db_path)
        try:
            # The worker, exactly as `pdf-splitter worker` runs it (sandbox launcher, real task).
            worker = Runner(settings, kinds=("analyze", "cut"))
            assert client.get(f"/api/jobs/{job_id}/analysis").status_code == 409
            assert worker.run_once(store) is True
            status = client.get(f"/api/jobs/{job_id}").json()
            assert (status["state"], status["kind"], status["progress"], status["total"]) == ("review", "analyze", 6, 6)

            analysis = client.get(f"/api/jobs/{job_id}/analysis").json()
            assert analysis["suggested"] == {"source": "headings", "level": 1}
            plan = client.get(f"/api/jobs/{job_id}/plan").json()
            assert plan["source"] == "headings" and len(plan["sections"]) == 3 and plan["overrides"] == {}
            assert plan["settings"] == DEFAULT_SETTINGS

            r = client.put(f"/api/jobs/{job_id}/plan", json=plan)
            assert r.status_code == 200 and r.json() == plan

            r = client.post(f"/api/jobs/{job_id}/cut")
            assert r.status_code == 202 and r.json() == {"id": job_id, "state": "queued"}
            status = client.get(f"/api/jobs/{job_id}").json()
            assert (status["state"], status["kind"], status["progress"], status["total"]) == ("queued", "cut", 0, 0)
            assert client.get(f"/api/jobs/{job_id}/result.zip").status_code == 409
            assert worker.run_once(store) is True
            status = client.get(f"/api/jobs/{job_id}").json()
            assert (status["state"], status["kind"], status["progress"], status["total"]) == ("done", "cut", 3, 3)
        finally:
            store.close()

        r = client.get(f"/api/jobs/{job_id}/result.zip")
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        assert r.headers["content-disposition"] == "attachment; filename*=utf-8''My%20Book-sections.zip"
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = zf.namelist()
            manifest = json.loads(zf.read(MANIFEST))
            pdfs = [n for n in names if n.endswith(".pdf")]
            assert len(pdfs) == 3 and all(zf.read(n).startswith(b"%PDF-") for n in pdfs)
        assert names == [zip_entry(i, s["name"]) for i, s in enumerate(plan["sections"])] + [MANIFEST]
        assert [m["index"] for m in manifest] == [0, 1, 2]
        assert [m["file"] for m in manifest] == pdfs
        assert sum(len(m["leaks"]) for m in manifest) == 0

        r = client.get(f"/api/jobs/{job_id}/sections/1.pdf")
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
        assert r.headers["content-disposition"] == f"attachment; filename*=utf-8''{zip_entry(1, plan['sections'][1]['name']).replace(' ', '%20').replace(':', '%3A')}"
        assert int(r.headers["content-length"]) == len(r.content) == manifest[1]["bytes"]
        assert r.content.startswith(b"%PDF-")
        assert_error(client.get(f"/api/jobs/{job_id}/sections/3.pdf"), 404, "not_found")

        r = client.delete(f"/api/jobs/{job_id}")
        assert r.status_code == 204
        assert not (settings.jobs_dir / job_id).exists()
        assert_error(client.get(f"/api/jobs/{job_id}"), 410, "expired")
        assert_error(client.get(f"/api/jobs/{job_id}/result.zip"), 410, "expired")


# ── GET /api/jobs/{id}: the polling contract ──────────────────────────────────────────────────────────


def test_job_status_shape_hides_the_requeue_marker_and_shows_failures(settings: Settings, seeded: Path) -> None:
    with api(settings) as client:
        r = client.get(f"/api/jobs/{DASH_ID}")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == STATUS_KEYS
        assert body["id"] == DASH_ID and body["state"] == "review" and body["kind"] == "analyze"
        assert (body["pages"], body["filename"], body["queue_position"]) == (6, "My Book.pdf", None)
        assert body["error_code"] is None and body["expires_at"] > datetime.now(UTC).isoformat()

        store = Store(settings.db_path)
        store.set_state(DASH_ID, "queued", error_code="requeued")        # the worker's sweep marker
        assert client.get(f"/api/jobs/{DASH_ID}").json()["error_code"] is None
        store.set_state(DASH_ID, "running")
        store.update_progress(DASH_ID, 2, 6, "Indexing pages")
        body = client.get(f"/api/jobs/{DASH_ID}").json()
        assert (body["state"], body["progress"], body["total"], body["message"]) == ("running", 2, 6, "Indexing pages")
        store.set_state(DASH_ID, "failed", error_code="resources", message="The PDF needed more memory.")
        body = client.get(f"/api/jobs/{DASH_ID}").json()
        assert (body["state"], body["error_code"], body["message"]) == ("failed", "resources", "The PDF needed more memory.")
        store.close()


def test_unknown_deleted_and_expired_jobs(settings: Settings, seeded: Path) -> None:
    with api(settings) as client:
        assert_error(client.get(f"/api/jobs/{OTHER_ID}"), 404, "not_found")
        assert_error(client.get("/api/jobs/.."), 404, "not_found")
        store = Store(settings.db_path)
        store.create_job(job_id=OTHER_ID, ip_hash="h", filename="old.pdf", bytes=1, pages=1, ttl_hours=24,
                         now=datetime.now(UTC) - timedelta(days=2))
        store.close()
        for path in ("", "/analysis", "/plan", "/sheets/1.png", "/result.zip", "/sections/0.pdf"):
            assert_error(client.get(f"/api/jobs/{OTHER_ID}{path}"), 410, "expired")
        assert_error(client.post(f"/api/jobs/{OTHER_ID}/cut"), 410, "expired")
        assert_error(client.put(f"/api/jobs/{OTHER_ID}/plan", json={}), 410, "expired")
        assert client.delete(f"/api/jobs/{DASH_ID}").status_code == 204
        assert_error(client.get(f"/api/jobs/{DASH_ID}"), 410, "expired")
        assert client.delete(f"/api/jobs/{DASH_ID}").status_code == 204          # idempotent
        assert_error(client.delete(f"/api/jobs/{'z' * 22}"), 404, "not_found")


@pytest.mark.parametrize(("state", "kind", "status"), [
    ("queued", "analyze", 409), ("running", "analyze", 409), ("failed", "analyze", 409),
    ("review", "analyze", 200), ("queued", "cut", 200), ("running", "cut", 200), ("failed", "cut", 200),
    ("done", "cut", 200),
])
def test_analysis_and_plan_exist_from_review_on(
    settings: Settings, analyzed_template: Path, state: str, kind: str, status: int
) -> None:
    seed_job(settings, analyzed_template, DASH_ID, state=state, kind=kind)
    with api(settings) as client:
        for path in ("/analysis", "/plan", "/sheets/1.png"):
            r = client.get(f"/api/jobs/{DASH_ID}{path}")
            assert r.status_code == status, (path, r.text)
            if status != 200:
                assert r.json()["code"] == "not_ready"
        if status == 200:
            assert set(client.get(f"/api/jobs/{DASH_ID}/analysis").json()) == {
                "pages", "pageLabels", "size", "outline", "headings", "suggested"
            }
            assert set(client.get(f"/api/jobs/{DASH_ID}/plan").json()) == {"source", "settings", "sections", "overrides"}


# ── AC-1: PUT /plan validation ────────────────────────────────────────────────────────────────────────


def plan_with(**changes) -> dict:
    base = {"source": "headings", "settings": dict(DEFAULT_SETTINGS), "sections": [{"name": "A", "page": 1}], "overrides": {}}
    return {**base, **changes}


@pytest.mark.parametrize(
    ("body", "expected_locs"),
    [
        (plan_with(source="toc"), [["body", "source"]]),
        (plan_with(settings={**DEFAULT_SETTINGS, "column_split": 0.19}), [["body", "settings", "column_split"]]),
        (plan_with(settings={**DEFAULT_SETTINGS, "column_split": 0.81}), [["body", "settings", "column_split"]]),
        (plan_with(settings={**DEFAULT_SETTINGS, "header_band": -0.1}), [["body", "settings", "header_band"]]),
        (plan_with(settings={**DEFAULT_SETTINGS, "footer_band": 200.5}), [["body", "settings", "footer_band"]]),
        (plan_with(settings={**DEFAULT_SETTINGS, "heading_min_size": 3.9}), [["body", "settings", "heading_min_size"]]),
        (plan_with(settings={**DEFAULT_SETTINGS, "heading_min_size": 72.1}), [["body", "settings", "heading_min_size"]]),
        (plan_with(settings={**DEFAULT_SETTINGS, "redact_top": 40}), [["body", "settings", "redact_top"]]),
        (plan_with(sections=[{"name": "A", "page": 0}]), [["body", "sections", 0, "page"]]),
        (plan_with(sections=[{"name": "A", "page": 7}]), [["body", "sections", 0, "page"]]),
        (plan_with(sections=[{"name": "A", "page": 1.5}]), [["body", "sections", 0, "page"]]),
        (plan_with(sections=[{"name": "", "page": 1}]), [["body", "sections", 0, "name"]]),
        (plan_with(sections=[{"name": "  ", "page": 1}]), [["body", "sections", 0, "name"]]),
        (plan_with(sections=[{"name": "n" * 121, "page": 1}]), [["body", "sections", 0, "name"]]),
        (plan_with(sections=[{"name": "A", "page": 1, "heading": "h" * 501}]), [["body", "sections", 0, "heading"]]),
        (plan_with(sections=[{"name": "A", "page": 1, "extra": 1}]), [["body", "sections", 0, "extra"]]),
        (plan_with(sections=[{"name": "A", "page": 1}] * 2001), [["body", "sections"]]),
        (plan_with(overrides={"1": {"startCut": 10}}), [["body", "overrides"]]),
        (plan_with(overrides={"-1": {"startCut": 10}}), [["body", "overrides"]]),
        (plan_with(overrides={"01": {"startCut": 10}}), [["body", "overrides"]]),
        (plan_with(overrides={"0": {"startCol": "middle"}}), [["body", "overrides", "0", "startCol"]]),
        (plan_with(overrides={"0": {"endCol": "both"}}), [["body", "overrides", "0", "endCol"]]),
        (plan_with(overrides={"0": {"startCut": -1}}), [["body", "overrides", "0", "startCut"]]),
        (plan_with(overrides={"0": {"startCut": 789.7}}), [["body", "overrides"]]),     # the page is 789.6 tall
        (plan_with(overrides={"0": {"endCut": 790}}), [["body", "overrides"]]),
        (plan_with(overrides={"0": {"pages": [1, 2]}}), [["body", "overrides", "0", "pages"]]),
        (plan_with(sections="A"), [["body", "sections"]]),
        ({"sections": []}, [["body", "source"]]),
    ],
)
def test_put_plan_rejects_bad_plans_with_field_errors(settings: Settings, seeded: Path, body: dict, expected_locs: list) -> None:
    before = (seeded / "plan.json").read_bytes()
    with api(settings) as client:
        r = client.put(f"/api/jobs/{DASH_ID}/plan", json=body)
    assert_error(r, 422, "invalid")
    assert locs(r) == expected_locs, r.json()
    assert all(set(e) == {"loc", "msg", "type"} for e in r.json()["errors"])
    assert (seeded / "plan.json").read_bytes() == before
    assert row(settings, DASH_ID)["state"] == "review"


@pytest.mark.parametrize(
    ("raw", "loc"),
    [
        ('"settings": {"column_split": NaN}', ["body", "settings", "column_split"]),
        ('"settings": {"header_band": Infinity}', ["body", "settings", "header_band"]),
        ('"settings": {"heading_min_size": -Infinity}', ["body", "settings", "heading_min_size"]),
        ('"overrides": {"0": {"startCut": NaN}}', ["body", "overrides", "0", "startCut"]),
        ('"overrides": {"0": {"endCut": 1e999}}', ["body", "overrides", "0", "endCut"]),
    ],
)
def test_put_plan_rejects_non_finite_numbers(settings: Settings, seeded: Path, raw: str, loc: list) -> None:
    body = '{"source": "manual", "sections": [{"name": "A", "page": 1}], ' + raw + "}"
    with api(settings) as client:
        r = client.put(f"/api/jobs/{DASH_ID}/plan", content=body, headers=JSON)
    assert_error(r, 422, "invalid")
    assert locs(r) == [loc] and r.json()["errors"][0]["type"] == "finite_number"


def test_put_plan_rejects_bodies_that_are_not_an_object(settings: Settings, seeded: Path) -> None:
    with api(settings) as client:
        assert_error(client.put(f"/api/jobs/{DASH_ID}/plan", content=b"[1]", headers=JSON), 422, "invalid")
        assert_error(client.put(f"/api/jobs/{DASH_ID}/plan", content=b"{oops", headers=JSON), 422, "invalid")
        assert_error(client.put(f"/api/jobs/{DASH_ID}/plan"), 422, "invalid")


def test_put_plan_normalizes_names_and_returns_the_saved_plan(settings: Settings, seeded: Path) -> None:
    # A raw body: JSON `"\udcff"` decodes to a lone surrogate no strict UTF-8 output can carry.
    body = (
        '{"source": "manual", "settings": {"single_column": true},'
        ' "sections": [{"name": " Intro\\udcff ", "page": 1, "heading": "In\\ud800tro"},'
        ' {"name": "Intro\ufffd", "page": 3}, {"name": "Intro\ufffd", "page": 4, "heading": ""}],'
        ' "overrides": {"2": {"startCut": null, "endCut": 700.25, "endCol": "left"}}}'
    ).encode()
    with api(settings) as client:
        r = client.put(f"/api/jobs/{DASH_ID}/plan", content=body, headers=JSON)
        assert r.status_code == 200, r.text
        saved = r.json()
        assert client.get(f"/api/jobs/{DASH_ID}/plan").json() == saved
    assert saved == {
        "source": "manual",
        "settings": {**DEFAULT_SETTINGS, "single_column": True},
        "sections": [
            {"name": "Intro�", "page": 1, "heading": "In�tro"},
            {"name": "Intro� (2)", "page": 3, "heading": ""},
            {"name": "Intro� (3)", "page": 4, "heading": ""},
        ],
        "overrides": {"2": {"startCut": None, "endCut": 700.25, "endCol": "left"}},   # only the keys sent
    }
    assert json.loads((seeded / "plan.json").read_bytes().decode("utf-8")) == saved   # strict decode
    assert not list(seeded.glob("*.tmp"))
    # The settings the engine will index under are exactly the saved ones.
    profile_from_dict(saved["settings"])


def test_put_plan_deduplicates_long_names_within_the_cap(settings: Settings, seeded: Path) -> None:
    name = "n" * 120
    with api(settings) as client:
        r = client.put(f"/api/jobs/{DASH_ID}/plan", json=plan_with(sections=[{"name": name, "page": p} for p in (1, 2, 3)]))
    assert r.status_code == 200
    names = [s["name"] for s in r.json()["sections"]]
    assert names == [name, "n" * 116 + " (2)", "n" * 116 + " (3)"] and len(set(names)) == 3


@pytest.mark.parametrize(("state", "kind", "code"), [
    ("queued", "analyze", "busy"), ("running", "analyze", "busy"), ("queued", "cut", "busy"),
    ("running", "cut", "busy"), ("failed", "analyze", "not_ready"),
])
def test_put_plan_and_cut_refused_outside_review_and_done(
    settings: Settings, analyzed_template: Path, state: str, kind: str, code: str
) -> None:
    seed_job(settings, analyzed_template, DASH_ID, state=state, kind=kind)
    with api(settings) as client:
        assert_error(client.put(f"/api/jobs/{DASH_ID}/plan", json=plan_with()), 409, code)
        assert_error(client.post(f"/api/jobs/{DASH_ID}/cut"), 409, code)
    assert row(settings, DASH_ID)["state"] == state


def test_put_plan_from_done_returns_the_job_to_review(settings: Settings, analyzed_template: Path) -> None:
    job_dir = seed_job(settings, analyzed_template, DASH_ID, state="done", kind="cut")
    (job_dir / "result.zip").write_bytes(b"old zip")
    with api(settings) as client:
        assert client.put(f"/api/jobs/{DASH_ID}/plan", json=plan_with()).status_code == 200
        assert client.get(f"/api/jobs/{DASH_ID}").json()["state"] == "review"
        # Old outputs stay downloadable until the next cut replaces them.
        assert client.get(f"/api/jobs/{DASH_ID}/result.zip").content == b"old zip"


# ── POST /cut ──────────────────────────────────────────────────────────────────────────────────────────


def test_cut_queues_once_and_resets_the_row(settings: Settings, seeded: Path) -> None:
    store = Store(settings.db_path)
    store.set_state(DASH_ID, "review", error_code="requeued", message="stale")
    store.update_progress(DASH_ID, 6, 6, "Finding headings")
    store.close()
    with api(settings) as client:
        r = client.post(f"/api/jobs/{DASH_ID}/cut")
        assert r.status_code == 202 and r.json() == {"id": DASH_ID, "state": "queued"}
        job = row(settings, DASH_ID)
        assert (job["state"], job["kind"], job["error_code"], job["message"]) == ("queued", "cut", None, None)
        assert (job["progress"], job["total"]) == (0, 0)
        assert_error(client.post(f"/api/jobs/{DASH_ID}/cut"), 409, "busy")
        assert client.get("/api/health").json()["queue"] == 1


def test_cut_refuses_an_empty_plan(settings: Settings, seeded: Path) -> None:
    with api(settings) as client:
        assert client.put(f"/api/jobs/{DASH_ID}/plan", json=plan_with(source="manual", sections=[])).status_code == 200
        r = client.post(f"/api/jobs/{DASH_ID}/cut")
        assert_error(r, 422, "invalid")
        assert locs(r) == [["plan", "sections"]]
    assert row(settings, DASH_ID)["state"] == "review"


def test_cut_from_done_recuts(settings: Settings, analyzed_template: Path) -> None:
    seed_job(settings, analyzed_template, DASH_ID, state="done", kind="cut")
    with api(settings) as client:
        assert client.post(f"/api/jobs/{DASH_ID}/cut").status_code == 202
    assert row(settings, DASH_ID)["state"] == "queued"


# ── AC-2: sheet PNGs ───────────────────────────────────────────────────────────────────────────────────


def test_sheet_png_renders_through_the_sandboxed_subprocess_and_caches(
    settings: Settings, seeded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    real_run = subprocess.run

    def spy(cmd, **kw):
        calls.append(cmd)
        assert kw["timeout"] == preview_routes.PREVIEW_TIMEOUT == 20.0
        return real_run(cmd, **kw)

    monkeypatch.setattr(preview_routes.subprocess, "run", spy)
    settings_hash = profile_from_dict(read_json(seeded / "plan.json")["settings"]).sha256[:12]
    with api(settings) as client:
        r = client.get(f"/api/jobs/{DASH_ID}/sheets/3.png?dpi=48")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert r.content.startswith(b"\x89PNG") and "private" in r.headers["cache-control"]
        cached = seeded / "png" / "48" / f"3-{settings_hash}.png"
        assert cached.read_bytes() == r.content
        assert not list(cached.parent.glob("*.tmp"))
        assert client.get(f"/api/jobs/{DASH_ID}/sheets/3.png?dpi=48").content == r.content
        assert len(calls) == 1                                     # the second hit is the cache
        assert client.get(f"/api/jobs/{DASH_ID}/sheets/3.png?dpi=110").status_code == 200
        assert len(calls) == 2 and (seeded / "png" / "110" / f"3-{settings_hash}.png").exists()
        assert client.get(f"/api/jobs/{DASH_ID}/sheets/3.png").status_code == 200     # dpi defaults to 72
        assert len(calls) == 3 and (seeded / "png" / "72" / f"3-{settings_hash}.png").exists()
    cmd = calls[0]
    # The same rlimits as the worker (AS 2 GB, CPU timeout + 10 s, FSIZE 1 GB), `--` before the job dir.
    assert cmd[1:3] == ["-m", "pdf_splitter.worker.sandbox"]
    assert cmd[cmd.index("--as") + 1] == str(2 * 1024**3)
    assert cmd[cmd.index("--cpu") + 1] == "30"
    assert cmd[cmd.index("--fsize") + 1] == str(1024**3)
    assert cmd[cmd.index("--") + 1 :] == ["-m", "pdf_splitter.preview", "sheet", "--", str(seeded)]


def test_sheet_png_cache_key_follows_the_saved_settings(settings: Settings, seeded: Path) -> None:
    with api(settings) as client:
        assert client.get(f"/api/jobs/{DASH_ID}/sheets/1.png?dpi=48").status_code == 200
        assert client.put(f"/api/jobs/{DASH_ID}/plan", json=plan_with(settings={**DEFAULT_SETTINGS, "header_band": 60})).status_code == 200
        assert client.get(f"/api/jobs/{DASH_ID}/sheets/1.png?dpi=48").status_code == 200
    assert len(list((seeded / "png" / "48").glob("1-*.png"))) == 2


@pytest.mark.parametrize("query", ["dpi=73", "dpi=0", "dpi=x", "dpi=72.5"])
def test_sheet_png_rejects_other_dpis(settings: Settings, seeded: Path, query: str) -> None:
    with api(settings) as client:
        r = client.get(f"/api/jobs/{DASH_ID}/sheets/1.png?{query}")
    assert_error(r, 422, "invalid")
    assert locs(r) == [["query", "dpi"]]


@pytest.mark.parametrize(("n", "status"), [("0", 404), ("7", 404), ("-1", 404), ("x", 422), ("1.5", 422)])
def test_sheet_png_validates_the_sheet_against_the_job(settings: Settings, seeded: Path, n: str, status: int) -> None:
    with api(settings) as client:
        r = client.get(f"/api/jobs/{DASH_ID}/sheets/{n}.png?dpi=72")
    assert_error(r, status, "not_found" if status == 404 else "invalid")
    assert not (seeded / "png").exists()


def test_preview_failures_are_500_preview_failed_and_logged_without_the_id(
    settings: Settings, seeded: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    (seeded / "source.pdf").write_bytes(b"%PDF-1.7 garbage")      # the subprocess crashes on it
    with api(settings) as client:
        assert_error(client.get(f"/api/jobs/{DASH_ID}/sheets/1.png?dpi=48"), 500, "preview_failed")
        assert_error(client.post(f"/api/jobs/{DASH_ID}/sections/0/plan"), 500, "preview_failed")
        monkeypatch.setattr(preview_routes, "PREVIEW_TIMEOUT", 0.05)   # a timeout is the same answer
        assert_error(client.get(f"/api/jobs/{DASH_ID}/sheets/2.png?dpi=48"), 500, "preview_failed")
    assert not (seeded / "png").exists()
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 3 and "timed out" in warnings[2] and "Traceback" in warnings[0]
    server = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("httpx"))
    assert log_id(DASH_ID) in server
    assert_id_gone(server, DASH_ID)


# ── Gate r1: a preview never resurrects a deleted job ─────────────────────────────────────────────────


def delete_now(settings: Settings) -> None:
    """The real DELETE (row first, then the directory), from wherever the test needs the race to happen."""
    store = Store(settings.db_path)
    try:
        download_routes.delete_job(DASH_ID, settings, store)
    finally:
        store.close()


def preview_in_process(monkeypatch: pytest.MonkeyPatch, once_open: Callable[[], None]) -> None:
    """Run the preview's `main` inside this process instead of the sandboxed subprocess (whose argv is pinned
    by `test_sheet_png_renders_…`), with `once_open` called as soon as the document is open: the only way to
    put a DELETE between the engine opening the PDF and it writing under the job directory."""
    real_open = pymupdf.open

    def hooked_open(*args, **kwargs):
        doc = real_open(*args, **kwargs)
        once_open()
        return doc

    def fake_run(cmd, *, input, **kw):
        args = cmd[cmd.index("--") + 1 :]
        assert args[:2] == ["-m", "pdf_splitter.preview"]
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdin", io.StringIO(input.decode()))
        with contextlib.redirect_stdout(out):
            rc = preview.main(args[2:])
        return subprocess.CompletedProcess(cmd, rc, stdout=out.getvalue().encode(), stderr=b"")

    monkeypatch.setattr(pymupdf, "open", hooked_open)
    monkeypatch.setattr(fitz, "open", hooked_open)             # the engine's `Book.open` goes through `fitz`
    monkeypatch.setattr(preview_routes.subprocess, "run", fake_run)


def test_a_sheet_render_that_outlives_a_delete_is_410_and_leaves_no_directory(
    settings: Settings, seeded: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    preview_in_process(monkeypatch, lambda: delete_now(settings))
    with api(settings) as client:
        assert_error(client.get(f"/api/jobs/{DASH_ID}/sheets/2.png?dpi=48"), 410, "expired")
    assert not seeded.exists()                                 # `<jobs>/<id>/png/48/` was never recreated
    assert row(settings, DASH_ID)["state"] == "deleted"
    server = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("httpx"))
    assert_id_gone(server, DASH_ID)


def test_a_section_plan_whose_engine_outlives_a_delete_is_410_and_leaves_no_directory(
    settings: Settings, seeded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preview_in_process(monkeypatch, lambda: delete_now(settings))
    with api(settings) as client:
        assert_error(client.post(f"/api/jobs/{DASH_ID}/sections/1/plan"), 410, "expired")
    assert not seeded.exists()                                 # nor `<jobs>/<id>/work/` with a fresh index
    assert row(settings, DASH_ID)["state"] == "deleted"


def test_a_sheet_deleted_after_its_render_is_410_not_500(
    settings: Settings, seeded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_run = subprocess.run

    def run_then_delete(cmd, **kw):
        proc = real_run(cmd, **kw)                             # the real subprocess wrote the PNG …
        delete_now(settings)                                   # … and the DELETE landed before it was served
        return proc

    monkeypatch.setattr(preview_routes.subprocess, "run", run_then_delete)
    with api(settings) as client:
        assert_error(client.get(f"/api/jobs/{DASH_ID}/sheets/1.png?dpi=48"), 410, "expired")
    assert not seeded.exists()


def test_a_section_plan_after_a_delete_is_410_not_500(
    settings: Settings, seeded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_run = subprocess.run

    def delete_then_run(cmd, **kw):
        delete_now(settings)                                   # the row and the directory go while we launch
        return real_run(cmd, **kw)

    monkeypatch.setattr(preview_routes.subprocess, "run", delete_then_run)
    with api(settings) as client:
        assert_error(client.post(f"/api/jobs/{DASH_ID}/sections/0/plan"), 410, "expired")
        assert_error(client.get(f"/api/jobs/{DASH_ID}/sheets/1.png?dpi=48"), 410, "expired")
    assert not seeded.exists()


# ── AC-3: section plans ────────────────────────────────────────────────────────────────────────────────


def test_section_plan_returns_the_engine_view_with_rects(settings: Settings, seeded: Path) -> None:
    plan_before = (seeded / "plan.json").read_bytes()
    with api(settings) as client:
        r = client.post(f"/api/jobs/{DASH_ID}/sections/1/plan")
        assert r.status_code == 200, r.text
        view = r.json()
        assert set(view) == SECTION_PLAN_KEYS
        # Chapter Two starts at the top of sheet 3 and ends where Closing Chapter starts mid-right on sheet 4.
        assert view["pages"] == [3, 4]
        assert view["startCut"] is None and view["startCol"] == "full"
        assert view["endCol"] == "right" and 300 < view["endCut"] < 500
        assert view["flags"] == [] and view["notes"] == []
        assert len(view["rects"]) == 1
        sheet, (x0, y0, x1, y1) = view["rects"][0]
        assert sheet == 4 and y0 == view["endCut"] and x0 > 250 and x1 > 500 and y1 > 700

        # An override on the request is applied to the view and persisted nowhere.
        r = client.post(f"/api/jobs/{DASH_ID}/sections/1/plan", json={"override": {"startCut": 200, "startCol": "left"}})
        assert r.status_code == 200
        with_override = r.json()
        assert (with_override["startCut"], with_override["startCol"]) == (200.0, "left")
        assert "override" in with_override["flags"] and with_override["endCut"] == view["endCut"]
        assert [s for s, _ in with_override["rects"]] == [3, 3, 4]
        assert client.post(f"/api/jobs/{DASH_ID}/sections/1/plan", json={}).json() == view
    assert (seeded / "plan.json").read_bytes() == plan_before
    assert read_json(seeded / "work" / OVERRIDES_NAME) == {}
    assert row(settings, DASH_ID)["state"] == "review"


def test_section_plan_uses_the_saved_override_unless_told_otherwise(settings: Settings, seeded: Path) -> None:
    with api(settings) as client:
        plan = read_json(seeded / "plan.json")
        plan["overrides"] = {"1": {"startCut": 150}}
        assert client.put(f"/api/jobs/{DASH_ID}/plan", json=plan).status_code == 200
        saved = client.post(f"/api/jobs/{DASH_ID}/sections/1/plan").json()
        assert saved["startCut"] == 150.0 and "override" in saved["flags"]
        auto = client.post(f"/api/jobs/{DASH_ID}/sections/1/plan", json={"override": None}).json()
        assert auto["startCut"] is None and "override" not in auto["flags"]
        given = client.post(f"/api/jobs/{DASH_ID}/sections/1/plan", json={"override": {"startCut": 99}}).json()
        assert given["startCut"] == 99.0
        assert client.post(f"/api/jobs/{DASH_ID}/sections/0/plan").json()["startCut"] is None


def test_section_plan_under_other_settings(settings: Settings, seeded: Path) -> None:
    with api(settings) as client:
        single = client.post(f"/api/jobs/{DASH_ID}/sections/1/plan", json={"settings": {"single_column": True}})
        assert single.status_code == 200, single.text
        assert single.json()["endCol"] == "full"           # every cut spans the page in single-column mode
        assert single.json()["rects"][0][1][0] == 0
    # The saved plan and its settings are untouched; the index cache now belongs to the preview's profile.
    assert read_json(seeded / "plan.json")["settings"] == DEFAULT_SETTINGS


@pytest.mark.parametrize(
    ("i", "body", "status", "code"),
    [
        ("3", {}, 404, "not_found"),
        ("-1", {}, 404, "not_found"),
        ("x", {}, 422, "invalid"),
        ("0", {"override": {"startCut": 9000}}, 422, "invalid"),
        ("0", {"override": {"startCol": "up"}}, 422, "invalid"),
        ("0", {"settings": {"column_split": 0.9}}, 422, "invalid"),
        ("0", {"settings": {"column_split": "NaN"}}, 422, "invalid"),
        ("0", {"other": 1}, 422, "invalid"),
        ("0", [1], 422, "invalid"),
    ],
)
def test_section_plan_validates_index_settings_and_override(
    settings: Settings, seeded: Path, i: str, body, status: int, code: str
) -> None:
    with api(settings) as client:
        r = client.post(f"/api/jobs/{DASH_ID}/sections/{i}/plan", json=body)
    assert_error(r, status, code)


# ── AC-5: downloads ────────────────────────────────────────────────────────────────────────────────────


def test_downloads_before_a_cut_are_not_ready(settings: Settings, seeded: Path) -> None:
    with api(settings) as client:
        assert_error(client.get(f"/api/jobs/{DASH_ID}/result.zip"), 409, "not_ready")
        assert_error(client.get(f"/api/jobs/{DASH_ID}/sections/0.pdf"), 409, "not_ready")


def test_section_pdf_comes_from_the_zip_by_plan_index(settings: Settings, analyzed_template: Path) -> None:
    job_dir = seed_job(settings, analyzed_template, DASH_ID, state="done", kind="cut", filename="Ünïcode: book?.pdf")
    rows = [{"index": 0, "name": "A", "file": "001 - A.pdf"}, {"index": 2, "name": "Ç/é", "file": "003 - Ç-é.pdf"}]
    with zipfile.ZipFile(job_dir / "result.zip", "w") as zf:
        zf.writestr("001 - A.pdf", b"%PDF-a")
        zf.writestr("003 - Ç-é.pdf", b"%PDF-c" * 1000)
        zf.writestr(MANIFEST, json.dumps(rows))
    with api(settings) as client:
        r = client.get(f"/api/jobs/{DASH_ID}/result.zip")
        assert r.status_code == 200
        assert r.headers["content-disposition"] == "attachment; filename*=utf-8''%C3%9Cn%C3%AFcode%3A%20book%3F-sections.zip"
        r = client.get(f"/api/jobs/{DASH_ID}/sections/2.pdf")
        assert r.status_code == 200 and r.content == b"%PDF-c" * 1000
        assert r.headers["content-disposition"] == "attachment; filename*=utf-8''003%20-%20%C3%87-%C3%A9.pdf"
        assert r.headers["content-length"] == "6000"
        assert client.get(f"/api/jobs/{DASH_ID}/sections/0.pdf").content == b"%PDF-a"
        assert_error(client.get(f"/api/jobs/{DASH_ID}/sections/1.pdf"), 404, "not_found")   # not in the zip
        assert_error(client.get(f"/api/jobs/{DASH_ID}/sections/x.pdf"), 422, "invalid")


def test_delete_marks_the_row_before_removing_the_directory(settings: Settings, seeded: Path) -> None:
    with api(settings) as client:
        assert client.delete(f"/api/jobs/{DASH_ID}").status_code == 204
    assert not seeded.exists()
    assert row(settings, DASH_ID)["state"] == "deleted"


# ── AC-7: request ids and the 500 shape ───────────────────────────────────────────────────────────────


def test_unexpected_errors_return_500_json_with_a_request_id(settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    app = create_app(settings)

    @app.get("/api/boom/{job_id}")
    def boom(job_id: str) -> None:
        raise RuntimeError(f"cannot open {settings.jobs_dir}/{job_id}/source.pdf")

    with TestClient(app) as client:
        r = client.get(f"/api/boom/{DASH_ID}")
    assert r.status_code == 500
    body = r.json()
    assert set(body) == {"code", "message", "request_id"}
    assert body == {"code": "internal", "message": errors.MESSAGES["internal"], "request_id": r.headers["x-request-id"]}
    request_id = body["request_id"]
    assert len(request_id) == 32
    lines = [rec.getMessage() for rec in caplog.records if rec.name.startswith("pdf_splitter")]
    access = [line for line in lines if line.startswith("GET ")]
    assert access == [next(line for line in access if line.startswith(f"GET /api/boom/{log_id(DASH_ID)} 500 "))]
    assert access[0].endswith(f" {request_id}")
    tracebacks = [line for line in lines if line.startswith(f"request {request_id} failed: ")]
    assert len(tracebacks) == 1 and "RuntimeError" in tracebacks[0] and "cannot open" in tracebacks[0]
    for line in lines:
        assert_id_gone(line, DASH_ID)
    assert str(settings.jobs_dir) not in r.text


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("abcdefgh", True), ("A1-b2-C3-d4", True), ("x" * 64, True),
        ("abcdefg", False), ("x" * 65, False), ("has_underscore1", False), ("has space 1", False),
        (b"\xe9v\xe8nement1", False), ("", False),        # latin-1 on the wire
    ],
)
def test_every_response_carries_a_request_id(
    settings: Settings, given: str | bytes, kept: bool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    with api(settings) as client:
        r = client.get("/api/health", headers={"X-Request-ID": given})
        assert r.status_code == 200
        request_id = r.headers["x-request-id"]
        assert (request_id == given) is kept
        assert len(request_id) == 32 or kept
        assert_error(client.get("/api/jobs/nope"), 404, "not_found")
    access = [rec.getMessage() for rec in caplog.records if rec.name == "pdf_splitter.access"]
    assert access[0] == f"GET /api/health 200 {access[0].split()[3]} {request_id}"
    assert len({line.rsplit(" ", 1)[1] for line in access}) == 2          # one id per request


def test_api_logs_never_contain_a_job_id(settings: Settings, seeded: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with api(settings) as client:
        client.get(f"/api/jobs/{DASH_ID}")
        client.get(f"/api/jobs/{DASH_ID}/analysis")
        client.get(f"/api/jobs/{DASH_ID}/plan")
        client.put(f"/api/jobs/{DASH_ID}/plan", json=plan_with())
        client.get(f"/api/jobs/{DASH_ID}/sheets/1.png?dpi=48")
        client.post(f"/api/jobs/{DASH_ID}/sections/0/plan")
        client.post(f"/api/jobs/{DASH_ID}/cut")
        client.get(f"/api/jobs/{DASH_ID}/result.zip")
        client.delete(f"/api/jobs/{DASH_ID}")
    server = [r.getMessage() for r in caplog.records if not r.name.startswith("httpx")]
    assert len(server) >= 9
    for line in server:
        assert_id_gone(line, DASH_ID)
    assert any(line.startswith(f"DELETE /api/jobs/{log_id(DASH_ID)} 204 ") for line in server)
