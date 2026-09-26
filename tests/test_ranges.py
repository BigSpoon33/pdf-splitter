"""STORY-015 (ADR-009): the `ranges` Plan source — validation over `PUT /plan` (AC-2), the PyMuPDF cut (AC-3)
and PRD AC-14's loop through the API. A range plan's rows, ZIP and manifest have the chapter cut's shape."""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from fixtures.books import text_book
from helpers import DASH_ID, row, seed_job
from test_api_e2e import STATUS_KEYS, api, assert_error, locs, plan_with
from test_upload import job_count, job_dirs

from pdf_splitter.config import Settings
from pdf_splitter.files import MODE_FILE, read_json, read_mode, write_json
from pdf_splitter.store import Store
from pdf_splitter.worker import cut
from pdf_splitter.worker.analyze import DEFAULT_SETTINGS, INDEX_CACHE, analyze, default_plan
from pdf_splitter.worker.analyze import ranges_plan as empty_ranges_plan
from pdf_splitter.worker.cut import MANIFEST, cut_ranges, engine_name, zip_entry
from pdf_splitter.worker.runner import Runner

PAGES = 30
# PRD AC-14: `1-10, 15-20, 5-7` → 10, 6 and 3 pages; gaps (11–14) and an overlap (5–7 inside 1–10) included.
AC14 = [(1, 10), (15, 20), (5, 7)]
MANIFEST_KEYS = {"index", "name", "file", "printedPages", "pageCount", "flags", "notes", "leaks", "bytes"}


def ranges_plan(spans: list[tuple[int, int]], **changes) -> dict:
    sections = [{"name": f"Pages {a}–{b}", "page": a, "endPage": b} for a, b in spans]
    return plan_with(source="ranges", sections=sections, **changes)


def page_texts(pdf: bytes) -> list[str]:
    """The first line of every page: `text_book` writes `page {n} line 0 …` with n the 0-based sheet."""
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return [page.get_text().splitlines()[0] for page in doc]


def sheets_of(pdf: bytes) -> list[int]:
    """The 1-based source sheets a file holds, read back from the page text."""
    return [int(line.split()[1]) + 1 for line in page_texts(pdf)]


@pytest.fixture(scope="session")
def ranges_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 30-page plain-text book analyzed once, in the shape the analyze task leaves a job dir."""
    root = tmp_path_factory.mktemp("ranges")
    text_book(root / "source.pdf", PAGES)
    analysis = analyze(root / "source.pdf", root / "work")
    write_json(root / "analysis.json", analysis)
    write_json(root / "plan.json", default_plan(analysis))
    return root


@pytest.fixture
def wstore(settings: Settings) -> Iterator[Store]:
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    s = Store(settings.db_path)
    s.init()
    yield s
    s.close()


def seed_ranges_job(settings: Settings, template: Path, *, state: str = "review", kind: str = "analyze") -> Path:
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    job_dir = settings.jobs_dir / DASH_ID
    shutil.copytree(template, job_dir)
    store = Store(settings.db_path)
    try:
        store.init()
        store.create_job(
            job_id=DASH_ID, ip_hash="h", filename="Thirty.pdf", bytes=(job_dir / "source.pdf").stat().st_size,
            pages=PAGES, ttl_hours=24, state=state, kind=kind,
        )
    finally:
        store.close()
    return job_dir


@pytest.fixture
def seeded(settings: Settings, ranges_template: Path) -> Path:
    return seed_ranges_job(settings, ranges_template)


# ── AC-2: PUT /plan ───────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "expected_locs"),
    [
        (plan_with(source="ranges", sections=[{"name": "A", "page": 1}]), [["body", "sections", 0, "endPage"]]),
        (plan_with(source="ranges", sections=[{"name": "A", "page": 1, "endPage": None}]), [["body", "sections", 0, "endPage"]]),
        (plan_with(source="ranges", sections=[{"name": "A", "page": 5, "endPage": 4}]), [["body", "sections", 0, "endPage"]]),
        (plan_with(source="ranges", sections=[{"name": "A", "page": 1, "endPage": PAGES + 1}]), [["body", "sections", 0, "endPage"]]),
        (plan_with(source="ranges", sections=[{"name": "A", "page": 1, "endPage": 0}]), [["body", "sections", 0, "endPage"]]),
        (plan_with(source="ranges", sections=[{"name": "A", "page": 1, "endPage": 2.5}]), [["body", "sections", 0, "endPage"]]),
        (plan_with(source="ranges", sections=[{"name": "A", "page": 0, "endPage": 3}]), [["body", "sections", 0, "page"]]),
        # A ranges section list only some of whose rows are whole: every bad row is named.
        (
            plan_with(source="ranges", sections=[{"name": "A", "page": 1, "endPage": 3}, {"name": "B", "page": 4}]),
            [["body", "sections", 1, "endPage"]],
        ),
        (ranges_plan([(1, 3)], overrides={"0": {"startCut": 10}}), [["body", "overrides"]]),
        # `endPage` is FORBIDDEN outside ranges mode (the decision AC-2 leaves open): a chapter plan with one is 422.
        (plan_with(source="headings", sections=[{"name": "A", "page": 1, "endPage": 3}]), [["body", "sections", 0, "endPage"]]),
        (plan_with(source="manual", sections=[{"name": "A", "page": 1, "endPage": 1}]), [["body", "sections", 0, "endPage"]]),
        (plan_with(source="outline", sections=[{"name": "A", "page": 1, "endPage": 2}]), [["body", "sections", 0, "endPage"]]),
    ],
)
def test_put_plan_rejects_bad_range_plans_with_field_errors(settings: Settings, seeded: Path, body: dict, expected_locs: list) -> None:
    before = (seeded / "plan.json").read_bytes()
    with api(settings) as client:
        r = client.put(f"/api/jobs/{DASH_ID}/plan", json=body)
    assert_error(r, 422, "invalid")
    assert locs(r) == expected_locs, r.json()
    assert (seeded / "plan.json").read_bytes() == before
    assert row(settings, DASH_ID)["state"] == "review"


def test_put_plan_accepts_ranges_that_overlap_gap_and_stand_alone(settings: Settings, seeded: Path) -> None:
    body = plan_with(
        source="ranges",
        sections=[
            {"name": "Pages 1–10", "page": 1, "endPage": 10},
            {"name": "Pages 15–20", "page": 15, "endPage": 20, "heading": ""},
            {"name": "Pages 5–7", "page": 5, "endPage": 7},
            {"name": "Page 30", "page": 30, "endPage": 30},
            {"name": "Page 30", "page": 30, "endPage": 30},
        ],
    )
    with api(settings) as client:
        r = client.put(f"/api/jobs/{DASH_ID}/plan", json=body)
        assert r.status_code == 200, r.text
        saved = r.json()
        assert client.get(f"/api/jobs/{DASH_ID}/plan").json() == saved
    assert saved == {
        "source": "ranges",
        "settings": DEFAULT_SETTINGS,
        "sections": [
            {"name": "Pages 1–10", "page": 1, "heading": "", "endPage": 10},
            {"name": "Pages 15–20", "page": 15, "heading": "", "endPage": 20},
            {"name": "Pages 5–7", "page": 5, "heading": "", "endPage": 7},
            {"name": "Page 30", "page": 30, "heading": "", "endPage": 30},
            {"name": "Page 30 (2)", "page": 30, "heading": "", "endPage": 30},
        ],
        "overrides": {},
    }
    assert read_json(seeded / "plan.json") == saved


def test_put_plan_accepts_an_empty_ranges_plan_but_refuses_to_cut_it(settings: Settings, seeded: Path) -> None:
    """The SPA saves `ranges` with no sections first (so a reload keeps the mode) and fills them as the visitor types."""
    with api(settings) as client:
        r = client.put(f"/api/jobs/{DASH_ID}/plan", json=ranges_plan([]))
        assert r.status_code == 200 and r.json()["source"] == "ranges" and r.json()["sections"] == []
        r = client.post(f"/api/jobs/{DASH_ID}/cut")
    assert_error(r, 422, "invalid")
    assert locs(r) == [["plan", "sections"]]


def test_chapter_plans_never_carry_end_page(settings: Settings, seeded: Path) -> None:
    """A chapter section keeps exactly its three keys — an explicit `null` is the absent value, never a fourth key
    in `plan.json` (the index cache and every STORY-007..011 fixture read it byte for byte)."""
    body = plan_with(source="manual", sections=[{"name": "A", "page": 1, "endPage": None}, {"name": "B", "page": 4}])
    with api(settings) as client:
        r = client.put(f"/api/jobs/{DASH_ID}/plan", json=body)
        assert r.status_code == 200, r.text
    assert r.json()["sections"] == [{"name": "A", "page": 1, "heading": ""}, {"name": "B", "page": 4, "heading": ""}]
    assert b"endPage" not in (seeded / "plan.json").read_bytes()


def test_section_plan_preview_is_refused_for_a_ranges_plan(settings: Settings, seeded: Path) -> None:
    with api(settings) as client:
        assert client.put(f"/api/jobs/{DASH_ID}/plan", json=ranges_plan(AC14)).status_code == 200
        r = client.post(f"/api/jobs/{DASH_ID}/sections/0/plan", json={})
        assert_error(r, 422, "invalid")
        assert locs(r) == [["plan", "source"]]
        # Sheet renders are not about sections: they still work (nothing in the SPA asks for them in this mode).
        assert client.get(f"/api/jobs/{DASH_ID}/sheets/1.png?dpi=48").status_code == 200


def test_preview_request_sections_refuse_end_page(settings: Settings, analyzed_template: Path) -> None:
    """The client's local list in a preview body is validated like a chapter plan's: `endPage` is not a thing there."""
    seed_job(settings, analyzed_template, DASH_ID)
    with api(settings) as client:
        r = client.post(f"/api/jobs/{DASH_ID}/sections/0/plan", json={"sections": [{"name": "A", "page": 1, "endPage": 2}]})
    assert_error(r, 422, "invalid")
    assert locs(r) == [["body", "sections", 0, "endPage"]]


# ── AC-3: cut_ranges ──────────────────────────────────────────────────────────────────────────────────


def test_cut_ranges_copies_each_span_with_its_pages(settings: Settings, seeded: Path) -> None:
    plan = ranges_plan(AC14)
    seen: list[tuple[int, int, str]] = []
    rows, result = cut_ranges(seeded, plan, lambda done, total, msg: seen.append((done, total, msg)))
    assert [r["index"] for r in rows] == [0, 1, 2]
    assert [r["file"] for r in rows] == [zip_entry(i, s["name"]) for i, s in enumerate(plan["sections"])]
    assert [r["formula"] for r in rows] == [engine_name(i, s["name"]) for i, s in enumerate(plan["sections"])]
    assert [r["name"] for r in rows] == ["Pages 1–10", "Pages 15–20", "Pages 5–7"]
    assert [r["pageCount"] for r in rows] == [10, 6, 3]
    assert [r["printedPages"] for r in rows] == [[1, 10], [15, 20], [5, 7]]
    assert all(r["flags"] == [] and r["notes"] == [] and r["leaks"] == [] and r["bytes"] > 0 for r in rows)
    assert set(rows[0]) >= MANIFEST_KEYS | {"formula"}
    files = [(seeded / "work" / f"{r['formula']}.pdf").read_bytes() for r in rows]
    assert [sheets_of(pdf) for pdf in files] == [list(range(1, 11)), list(range(15, 21)), [5, 6, 7]]
    assert all(pdf.startswith(b"%PDF-") for pdf in files)
    assert result["missing"] == [] and result["leaks"] == {}
    assert seen[0] == (0, 3, cut.MSG_PREPARING)
    assert seen[1:] == [(1, 3, cut.MSG_CUTTING), (2, 3, cut.MSG_CUTTING), (3, 3, cut.MSG_CUTTING)]


def test_cut_ranges_every_ten_pages_gives_three_files(settings: Settings, seeded: Path) -> None:
    rows, _ = cut_ranges(seeded, ranges_plan([(1, 10), (11, 20), (21, 30)]), lambda *_: None)
    assert [r["pageCount"] for r in rows] == [10, 10, 10]
    assert [sheets_of((seeded / "work" / f"{r['formula']}.pdf").read_bytes())[-1] for r in rows] == [10, 20, 30]


def test_cut_ranges_resets_stale_outputs_like_the_engine_cut(settings: Settings, seeded: Path) -> None:
    work = seeded / "work"
    (work / "009-gone.pdf").write_bytes(b"%PDF-stale")
    write_json(work / cut.MANIFEST_NAME, [{"formula": "009-gone", "file": "009-gone.pdf"}])
    write_json(work / cut.OVERRIDES_NAME, {"001-x": {"startCut": 1.0}})
    rows, _ = cut_ranges(seeded, ranges_plan([(2, 2)]), lambda *_: None)
    assert not (work / "009-gone.pdf").exists()
    assert not (work / cut.MANIFEST_NAME).exists() and not (work / cut.OVERRIDES_NAME).exists()
    assert sorted(p.name for p in work.glob("*.pdf")) == [f"{rows[0]['formula']}.pdf"]
    assert (work / INDEX_CACHE).exists()        # the analyze index stays for a later chapter-mode switch


def test_runner_runs_a_ranges_cut_to_done_with_the_chapter_cuts_zip_shape(
    settings: Settings, wstore: Store, ranges_template: Path
) -> None:
    """`Runner` + the sandboxed task on a `ranges` plan: `result.zip` = `NNN - <name>.pdf` × 3 + manifest.json, the
    row ends `done` with progress 3/3, exactly as `tests/test_cut.py::test_runner_runs_the_real_cut_task_to_done`."""
    job_dir = seed_ranges_job(settings, ranges_template, state="queued", kind="cut")
    plan = ranges_plan(AC14)
    write_json(job_dir / "plan.json", plan)
    assert Runner(settings, kinds=("analyze", "cut")).run_once(wstore) is True
    job = wstore.get_job(DASH_ID)
    assert (job["state"], job["kind"], job["error_code"]) == ("done", "cut", None), job
    assert (job["progress"], job["total"], job["message"]) == (3, 3, None)
    with zipfile.ZipFile(job_dir / "result.zip") as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read(MANIFEST))
        pdfs = {n: zf.read(n) for n in names if n.endswith(".pdf")}
    assert names == [zip_entry(i, s["name"]) for i, s in enumerate(plan["sections"])] + [MANIFEST]
    assert [(m["index"], m["name"], m["file"]) for m in manifest] == [
        (i, s["name"], zip_entry(i, s["name"])) for i, s in enumerate(plan["sections"])
    ]
    assert all(set(m) >= MANIFEST_KEYS and m["flags"] == [] and m["leaks"] == [] for m in manifest)
    assert [sheets_of(pdfs[m["file"]]) for m in manifest] == [list(range(1, 11)), list(range(15, 21)), [5, 6, 7]]
    assert not list(job_dir.glob("*.tmp"))


# ── STORY-012 addendum: the output budget on the ranges path ──────────────────────────────────────────


def test_cut_ranges_stops_at_the_budget_and_leaves_no_span_behind(settings: Settings, seeded: Path) -> None:
    whole_book = (seeded / "source.pdf").stat().st_size
    plan = ranges_plan([(1, PAGES)] * 200)                       # 200 whole-book copies: ~200× the upload
    seen: list[tuple[int, int, str]] = []
    with pytest.raises(cut.OutputTooLarge):
        cut_ranges(seeded, plan, lambda d, t, m: seen.append((d, t, m)), limit=4 * whole_book)
    assert 3 <= len(seen) <= 8 and seen[-1][1] == 200          # stopped after a handful of spans, not 200
    assert list((seeded / "work").glob("*.pdf")) == []
    assert (seeded / "work" / INDEX_CACHE).exists()


def test_a_ranges_cut_over_the_budget_fails_cleanly_and_a_smaller_plan_cuts_from_the_same_page(
    settings: Settings, wstore: Store, seeded: Path
) -> None:
    """Through the API and the real runner: 200 whole-book spans → `failed/too_large_output`, no ZIP; the job is
    still editable (a failed cut is recoverable), a 3-span plan is saved and cut to `done` on the same page."""
    whole_book = (seeded / "source.pdf").stat().st_size
    capped = settings.model_copy(update={"max_output_bytes": 4 * whole_book})
    with api(capped) as client:
        assert client.put(f"/api/jobs/{DASH_ID}/plan", json=ranges_plan([(1, PAGES)] * 200)).status_code == 200
        assert client.post(f"/api/jobs/{DASH_ID}/cut").status_code == 202
        assert Runner(capped, kinds=("cut",)).run_once(wstore) is True
        body = client.get(f"/api/jobs/{DASH_ID}").json()
        assert (body["state"], body["kind"], body["error_code"]) == ("failed", "cut", "too_large_output")
        assert "fewer or smaller sections" in body["message"]
        assert_error(client.get(f"/api/jobs/{DASH_ID}/result.zip"), 409, "not_ready")
        assert not list(seeded.glob("*.tmp")) and list((seeded / "work").glob("*.pdf")) == []
        r = client.put(f"/api/jobs/{DASH_ID}/plan", json=ranges_plan(AC14))
        assert r.status_code == 200 and client.get(f"/api/jobs/{DASH_ID}").json()["state"] == "review"
        assert client.post(f"/api/jobs/{DASH_ID}/cut").status_code == 202
        assert Runner(capped, kinds=("cut",)).run_once(wstore) is True
        assert client.get(f"/api/jobs/{DASH_ID}").json()["state"] == "done"
        assert [m["pageCount"] for m in client.get(f"/api/jobs/{DASH_ID}/manifest").json()] == [10, 6, 3]


# ── PRD AC-14 through the API: upload → analyze → ranges plan → cut → download ────────────────────────


def test_ac14_upload_ranges_plan_cut_download(settings: Settings, tmp_path: Path) -> None:
    text_book(tmp_path / "thirty.pdf", PAGES)
    with api(settings) as client:
        r = client.post("/api/jobs", files={"file": ("Thirty.pdf", (tmp_path / "thirty.pdf").read_bytes(), "application/pdf")})
        assert r.status_code == 201, r.text
        job_id = r.json()["id"]
        store = Store(settings.db_path)
        try:
            worker = Runner(settings, kinds=("analyze", "cut"))
            assert worker.run_once(store) is True
            status = client.get(f"/api/jobs/{job_id}").json()
            assert set(status) == STATUS_KEYS and (status["state"], status["pages"]) == ("review", PAGES)
            # The upload analyzed as usual (ADR-009: same pipeline); the SPA then PUTs its first ranges plan.
            assert client.get(f"/api/jobs/{job_id}/plan").json()["source"] != "ranges"
            r = client.put(f"/api/jobs/{job_id}/plan", json=ranges_plan(AC14))
            assert r.status_code == 200 and r.json()["source"] == "ranges"
            plan = r.json()
            assert client.post(f"/api/jobs/{job_id}/cut").status_code == 202
            assert worker.run_once(store) is True
            status = client.get(f"/api/jobs/{job_id}").json()
            assert (status["state"], status["kind"], status["progress"], status["total"]) == ("done", "cut", 3, 3)
        finally:
            store.close()

        manifest = client.get(f"/api/jobs/{job_id}/manifest").json()
        assert [(m["index"], m["pageCount"]) for m in manifest] == [(0, 10), (1, 6), (2, 3)]
        r = client.get(f"/api/jobs/{job_id}/result.zip")
        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            pdfs = [zf.read(n) for n in zf.namelist() if n.endswith(".pdf")]
        assert [len(page_texts(p)) for p in pdfs] == [10, 6, 3]
        assert [sheets_of(p) for p in pdfs] == [list(range(1, 11)), list(range(15, 21)), [5, 6, 7]]
        r = client.get(f"/api/jobs/{job_id}/sections/1.pdf")
        assert r.status_code == 200 and sheets_of(r.content) == list(range(15, 21))
        assert r.headers["content-disposition"].startswith("attachment; filename*=utf-8''002%20-%20Pages%2015")

        # "every 10 pages" on the same file: a second cut of the same job replaces the ZIP.
        store = Store(settings.db_path)
        try:
            r = client.put(f"/api/jobs/{job_id}/plan", json=ranges_plan([(1, 10), (11, 20), (21, 30)]))
            assert r.status_code == 200 and client.get(f"/api/jobs/{job_id}").json()["state"] == "review"
            assert client.post(f"/api/jobs/{job_id}/cut").status_code == 202
            assert Runner(settings, kinds=("cut",)).run_once(store) is True
        finally:
            store.close()
        manifest = client.get(f"/api/jobs/{job_id}/manifest").json()
        assert [m["pageCount"] for m in manifest] == [10, 10, 10]
        with zipfile.ZipFile(io.BytesIO(client.get(f"/api/jobs/{job_id}/result.zip").content)) as zf:
            assert len([n for n in zf.namelist() if n.endswith(".pdf")]) == 3
        assert plan["sections"][0]["endPage"] == 10


# ── Gate r1: the mode is the job's, fixed at upload; no URL can convert a job ──────────────────────────


def upload(client, path: Path, **form) -> object:
    """`POST /api/jobs` with the optional `mode` form field next to the file."""
    return client.post("/api/jobs", files={"file": ("Thirty.pdf", path.read_bytes(), "application/pdf")}, data=form)


def test_upload_in_ranges_mode_analyzes_to_an_empty_ranges_plan_then_cuts_ac14(settings: Settings, tmp_path: Path) -> None:
    text_book(tmp_path / "thirty.pdf", PAGES)
    with api(settings) as client:
        r = upload(client, tmp_path / "thirty.pdf", mode="ranges")
        assert r.status_code == 201, r.text
        job_id = r.json()["id"]
        job_dir = settings.jobs_dir / job_id
        assert sorted(p.name for p in job_dir.iterdir()) == [MODE_FILE, "source.pdf"]
        assert read_mode(job_dir) == "ranges"
        store = Store(settings.db_path)
        try:
            worker = Runner(settings, kinds=("analyze", "cut"))
            assert worker.run_once(store) is True
            assert client.get(f"/api/jobs/{job_id}").json()["state"] == "review"
            # The analysis still exists (same pipeline), but the first plan is the mode's: no chapter suggestion to
            # convert, so opening the job — with any query — never writes anything.
            assert read_json(job_dir / "analysis.json")["pages"] == PAGES
            first = client.get(f"/api/jobs/{job_id}/plan").json()
            assert first == empty_ranges_plan() == {
                "source": "ranges", "settings": dict(DEFAULT_SETTINGS), "sections": [], "overrides": {},
            }
            # It is already what a PUT of itself normalizes to, so the SPA has nothing to save on load.
            r = client.put(f"/api/jobs/{job_id}/plan", json=first)
            assert r.status_code == 200 and r.json() == first
            r = client.post(f"/api/jobs/{job_id}/cut")
            assert_error(r, 422, "invalid")
            assert locs(r) == [["plan", "sections"]]
            r = client.put(f"/api/jobs/{job_id}/plan", json=ranges_plan(AC14))
            assert r.status_code == 200 and r.json()["source"] == "ranges"
            assert client.post(f"/api/jobs/{job_id}/cut").status_code == 202
            assert worker.run_once(store) is True
            assert client.get(f"/api/jobs/{job_id}").json()["state"] == "done"
        finally:
            store.close()
        manifest = client.get(f"/api/jobs/{job_id}/manifest").json()
        assert [(m["index"], m["pageCount"]) for m in manifest] == [(0, 10), (1, 6), (2, 3)]
        with zipfile.ZipFile(io.BytesIO(client.get(f"/api/jobs/{job_id}/result.zip").content)) as zf:
            pdfs = [zf.read(n) for n in zf.namelist() if n.endswith(".pdf")]
        assert [sheets_of(p) for p in pdfs] == [list(range(1, 11)), list(range(15, 21)), [5, 6, 7]]


# An empty field is what an HTML form sends for "nothing chosen": FastAPI reads it as absent, i.e. the default.
@pytest.mark.parametrize("form", [{}, {"mode": "chapters"}, {"mode": ""}], ids=["default", "explicit", "empty"])
def test_upload_in_chapter_mode_is_unchanged(settings: Settings, tmp_path: Path, form: dict) -> None:
    text_book(tmp_path / "thirty.pdf", PAGES)
    with api(settings) as client:
        r = upload(client, tmp_path / "thirty.pdf", **form)
        assert r.status_code == 201, r.text
        job_dir = settings.jobs_dir / r.json()["id"]
        # The default writes no marker: a chapter job's directory is byte-for-byte what it was before the fix.
        assert sorted(p.name for p in job_dir.iterdir()) == ["source.pdf"]
        assert read_mode(job_dir) == "chapters"
        store = Store(settings.db_path)
        try:
            assert Runner(settings, kinds=("analyze",)).run_once(store) is True
        finally:
            store.close()
        plan = client.get(f"/api/jobs/{r.json()['id']}/plan").json()
    assert plan == default_plan(read_json(job_dir / "analysis.json"))
    assert plan["source"] != "ranges"
    assert not (job_dir / MODE_FILE).exists()


@pytest.mark.parametrize("mode", ["pages", "RANGES", "chapters,ranges"])
def test_upload_refuses_an_unknown_mode_and_leaves_nothing_behind(settings: Settings, tmp_path: Path, mode: str) -> None:
    text_book(tmp_path / "thirty.pdf", 2)
    with api(settings) as client:
        r = upload(client, tmp_path / "thirty.pdf", mode=mode)
    assert_error(r, 422, "invalid")
    assert locs(r) == [["body", "mode"]]
    assert job_dirs(settings) == [] and job_count(settings) == 0
