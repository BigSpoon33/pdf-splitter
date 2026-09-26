"""STORY-007 AC-4: the cut task (`worker/cut.py`, `task.run_cut`) through the real sandboxed runner."""

from __future__ import annotations

import errno
import json
import logging
import subprocess
import zipfile
from pathlib import Path

import pymupdf
import pytest
from fixtures.books import text_book
from fixtures.hostile import link_uri_bomb
from helpers import DASH_ID, assert_id_gone, seed_job
from monograph_splitter.session import MANIFEST_NAME, OVERRIDES_NAME

from pdf_splitter.config import Settings
from pdf_splitter.files import read_json, write_json
from pdf_splitter.store import Store, log_id
from pdf_splitter.worker import cut, sandbox, task
from pdf_splitter.worker import runner as runner_mod
from pdf_splitter.worker.analyze import DEFAULT_SETTINGS
from pdf_splitter.worker.cut import (
    OutputTooLarge,
    cut_book,
    cut_ranges,
    engine_entries,
    engine_name,
    hit_file_limit,
    output_budget,
    write_zip,
    zip_entry,
)
from pdf_splitter.worker.runner import Runner, classify

GUARDED = "import sys; from pdf_splitter.worker.task import guarded; "


def ticks() -> tuple[list[tuple[int, int, str]], object]:
    seen: list[tuple[int, int, str]] = []
    return seen, lambda done, total, msg: seen.append((done, total, msg))


# ── names ─────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("index", "name", "expected"),
    [
        (0, "Foundations of Testing", "001-foundations-of-testing"),
        (1, "Chapter Two: The Middle / of the Book", "002-chapter-two-the-middle-of-the-book"),
        (2, "Zhōngyī 中医 – Kapitel 1", "003-zhongyi-kapitel-1"),
        (3, "中医", "004-section"),                     # nothing ASCII survives: still a name
        (4, "../..", "005-section"),
        (1999, "x", "2000-x"),
    ],
)
def test_engine_name_is_ascii_and_unique_by_prefix(index: int, name: str, expected: str) -> None:
    assert engine_name(index, name) == expected


def test_engine_name_stays_under_80_bytes() -> None:
    name = engine_name(7, "word " * 40)
    assert len(name.encode()) <= cut.ENGINE_NAME_BYTES and name.startswith("008-word-word") and not name.endswith("-")


@pytest.mark.parametrize(
    ("index", "name", "expected"),
    [
        (0, "Foundations of Testing", "001 - Foundations of Testing.pdf"),
        (1, "A/B\\C:D*E?F\"G<H>I|J", "002 - A-B-C-D-E-F-G-H-I-J.pdf"),
        (2, "  spaced \t out \x1b[31m ", "003 - spaced out [31m.pdf"),
        (3, "中医 – Kapitel", "004 - 中医 – Kapitel.pdf"),
        (4, "///", "005 - ---.pdf"),
        (5, "\x00\x01", "006 - section.pdf"),
    ],
)
def test_zip_entry_names_keep_book_order_and_are_safe(index: int, name: str, expected: str) -> None:
    assert zip_entry(index, name) == expected


def test_zip_entry_name_is_capped() -> None:
    entry = zip_entry(0, "n" * 300)
    assert len(entry) <= cut.ZIP_NAME_CHARS and entry.endswith(".pdf")


# ── cut_book (in process, on the analyzed template) ───────────────────────────────────────────────────


def test_cut_book_writes_every_section_and_translates_overrides(settings: Settings, analyzed_template: Path) -> None:
    job_dir = seed_job(settings, analyzed_template, DASH_ID)
    plan = read_json(job_dir / "plan.json")
    plan["overrides"] = {"1": {"startCut": 120.0, "startCol": "full"}}
    seen, progress = ticks()
    rows, result = cut_book(job_dir, plan, progress)
    assert [r["index"] for r in rows] == [0, 1, 2]
    assert [r["file"] for r in rows] == [zip_entry(i, s["name"]) for i, s in enumerate(plan["sections"])]
    assert [r["name"] for r in rows] == [s["name"] for s in plan["sections"]]
    assert rows[1]["startCut"] == 120.0 and "override" in rows[1]["flags"]
    assert "override" not in rows[0]["flags"] and "override" not in rows[2]["flags"]
    assert result["leaks"] == {} and result["missing"] == [] and result["unknown"] == []
    # The engine's files carry the engine names; the overrides file is keyed by them, not by the plan index.
    assert sorted(p.name for p in job_dir.glob("work/*.pdf")) == [f"{e['name']}.pdf" for e in engine_entries(plan["sections"])]
    assert list(read_json(job_dir / "work" / OVERRIDES_NAME)) == ["002-chapter-two-the-middle-of-the-synthetic-book"]
    assert seen[0] == (0, 3, cut.MSG_PREPARING)
    assert seen[1:] == [(1, 3, cut.MSG_CUTTING), (2, 3, cut.MSG_CUTTING), (3, 3, cut.MSG_CUTTING)]


def test_cut_book_reuses_the_analyze_index_and_leaves_stale_outputs_behind(
    settings: Settings, analyzed_template: Path
) -> None:
    job_dir = seed_job(settings, analyzed_template, DASH_ID)
    work = job_dir / "work"
    index_before = (work / ".book-index.json").read_bytes()
    # Left by an earlier cut of a longer plan with an override: Book.open would keep all of it.
    (work / "009-gone.pdf").write_bytes(b"%PDF-stale")
    write_json(work / MANIFEST_NAME, [{"formula": "009-gone", "file": "009-gone.pdf"}])
    write_json(work / OVERRIDES_NAME, {"001-foundations-of-testing": {"startCut": 1.0}})
    plan = read_json(job_dir / "plan.json")
    rows, _ = cut_book(job_dir, plan, lambda *_: None)
    assert "override" not in rows[0]["flags"]
    assert not (work / "009-gone.pdf").exists()
    assert [m["formula"] for m in read_json(work / MANIFEST_NAME)] == [e["name"] for e in engine_entries(plan["sections"])]
    assert read_json(work / OVERRIDES_NAME) == {}
    assert (work / ".book-index.json").read_bytes() == index_before      # the default settings hit the cache


# ── STORY-012 addendum: the output budget on the engine path ──────────────────────────────────────────


def test_output_budget_is_ten_uploads_within_a_floor_and_the_ceiling() -> None:
    mb = 1024 * 1024
    assert output_budget(2048 * mb, 1 * mb) == cut.OUTPUT_FLOOR                # a small book gets the floor
    assert output_budget(2048 * mb, 50 * mb) == 500 * mb                       # 10× the upload
    assert output_budget(4096 * mb, 300 * mb) == cut.OUTPUT_CEILING            # never past the ceiling
    assert output_budget(512 * mb, 300 * mb) == 512 * mb                       # a lower setting still wins
    assert output_budget(300_000, 100 * mb) == 300_000                         # a test-sized ceiling wins


def test_output_budget_never_exceeds_the_sandbox_file_limit() -> None:
    """Gate r1 finding 3: the budget reached ~2 GiB while the task's RLIMIT_FSIZE is 1 GiB, so the ZIP of a
    cut within budget hit EFBIG. The ceiling is the sandbox's constant less room for the ZIP — one source."""
    assert cut.OUTPUT_CEILING == sandbox.FSIZE_BYTES - cut.ZIP_MARGIN < sandbox.FSIZE_BYTES
    for setting, upload in ((2 * 1024**3, 200 * 1024**2), (2**62, 2**40), (Settings().max_output_bytes, 1)):
        assert output_budget(setting, upload) <= cut.OUTPUT_CEILING
    # The ceiling is what the default settings and any real upload land on: the setting no longer decides.
    assert output_budget(Settings().max_output_bytes, 200 * 1024**2) == cut.OUTPUT_CEILING


def test_cut_book_stops_at_the_budget_and_leaves_no_section_behind(settings: Settings, analyzed_template: Path) -> None:
    job_dir = seed_job(settings, analyzed_template, DASH_ID)
    work = job_dir / "work"
    index_before = (work / ".book-index.json").read_bytes()
    plan = read_json(job_dir / "plan.json")
    seen, progress = ticks()
    with pytest.raises(OutputTooLarge):
        cut_book(job_dir, plan, progress, limit=1)
    # The first section's file passed the budget as soon as it was written: nothing of the cut stays.
    assert seen == [(0, 3, cut.MSG_PREPARING)]
    assert list(work.glob("*.pdf")) == [] and not (work / MANIFEST_NAME).exists()
    assert (work / ".book-index.json").read_bytes() == index_before
    # Unbounded, or a budget the three sections fit in, cuts as before.
    rows, _ = cut_book(job_dir, plan, lambda *_: None, limit=None)
    assert len(rows) == 3
    rows, _ = cut_book(job_dir, plan, lambda *_: None, limit=cut.written_bytes(work))
    assert len(rows) == 3


def test_runner_fails_a_cut_over_the_budget_as_too_large_output_then_recuts_it(
    settings: Settings, wstore: Store, analyzed_template: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Through the real sandboxed task: `failed/too_large_output` with the runner's message, no `result.zip`,
    no `.tmp`, no section in `work/`; the budget reaches the task through its environment. Then the same job,
    re-queued from `failed` (the recoverable failed cut) under the default budget, cuts to `done`."""
    caplog.set_level(logging.WARNING)
    job_dir = seed_job(settings, analyzed_template, DASH_ID, state="queued", kind="cut")
    capped = settings.model_copy(update={"max_output_bytes": 1})
    assert Runner(capped, kinds=("cut",)).run_once(wstore) is True
    job = wstore.get_job(DASH_ID)
    assert (job["state"], job["kind"], job["error_code"]) == ("failed", "cut", "too_large_output"), job
    assert job["message"] == runner_mod.MESSAGES["too_large_output"]
    assert "fewer or smaller sections" in job["message"]
    assert not (job_dir / "result.zip").exists() and not list(job_dir.glob("*.tmp"))
    assert list((job_dir / "work").glob("*.pdf")) == []
    assert "failed: too_large_output" in caplog.text
    assert_id_gone(caplog.text, DASH_ID)
    assert wstore.transition(DASH_ID, "failed", "queued", kind="cut", expect_kind="cut")
    assert Runner(settings, kinds=("cut",)).run_once(wstore) is True
    job = wstore.get_job(DASH_ID)
    assert (job["state"], job["error_code"]) == ("done", None)
    assert (job_dir / "result.zip").exists()


# ── write_zip: atomic, and no .tmp after a failure that happens with the .tmp on disk ─────────────────


def test_write_zip_removes_the_tmp_when_the_rename_fails(
    settings: Settings, analyzed_template: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_dir = seed_job(settings, analyzed_template, DASH_ID)
    rows, _ = cut_book(job_dir, read_json(job_dir / "plan.json"), lambda *_: None)
    out, tmp = job_dir / "result.zip", job_dir / "result.zip.tmp"
    out.write_bytes(b"previous")
    seen: dict[str, list[str]] = {}

    def refuse(src: str | Path, dst: str | Path) -> None:
        seen["names"] = zipfile.ZipFile(src).namelist()     # proof the failure happens with a complete .tmp
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(cut.os, "replace", refuse)
    with pytest.raises(OSError, match="Input/output"):
        write_zip(out, job_dir / "work", rows)
    assert seen["names"] == [r["file"] for r in rows] + [cut.MANIFEST]
    assert not tmp.exists()
    assert out.read_bytes() == b"previous"


def test_write_zip_removes_a_tmp_cut_short_by_rlimit_fsize(settings: Settings, analyzed_template: Path) -> None:
    """The real failure: under the sandbox's RLIMIT_FSIZE the zip write fails with EFBIG part-way through;
    the task reports `resources`, the truncated `.tmp` is gone, the previous result survives."""
    job_dir = seed_job(settings, analyzed_template, DASH_ID)
    rows, _ = cut_book(job_dir, read_json(job_dir / "plan.json"), lambda *_: None)
    assert sum((job_dir / "work" / f"{r['formula']}.pdf").stat().st_size for r in rows) > 8192
    out, tmp = job_dir / "result.zip", job_dir / "result.zip.tmp"
    out.write_bytes(b"previous")
    code = (
        GUARDED + "import json; from pathlib import Path; from pdf_splitter.worker.cut import write_zip; "
        f"rows = json.loads({json.dumps(rows)!r}); "
        f"sys.exit(guarded(lambda: (write_zip(Path({str(out)!r}), Path({str(job_dir / 'work')!r}), rows), True)[1]))"
    )
    cmd = sandbox.command({**sandbox.limits(10), "fsize": 4096}, ["-c", code])
    proc = subprocess.run(cmd, capture_output=True, check=False, stdin=subprocess.DEVNULL, timeout=60)
    assert proc.returncode == task.EXIT_RESOURCES, proc.stderr
    assert classify(proc.returncode, proc.stdout) == "resources"
    assert not tmp.exists() and not list(job_dir.glob("*.tmp"))
    assert out.read_bytes() == b"previous"


def test_a_zip_the_sandbox_refuses_is_too_large_output_and_leaves_no_section(
    settings: Settings, analyzed_template: Path
) -> None:
    """Gate r1 finding 3, the cut's own packaging: the same EFBIG through `package` is the output being too
    large — `too_large_output`, the sections gone from `work/`, the `.tmp` gone, the previous result kept."""
    job_dir = seed_job(settings, analyzed_template, DASH_ID)
    rows, _ = cut_book(job_dir, read_json(job_dir / "plan.json"), lambda *_: None)
    assert len(list((job_dir / "work").glob("*.pdf"))) == 3
    out = job_dir / "result.zip"
    out.write_bytes(b"previous")
    code = (
        GUARDED + "import json; from pathlib import Path; from pdf_splitter.worker.cut import package; "
        f"rows = json.loads({json.dumps(rows)!r}); "
        f"sys.exit(guarded(lambda: (package(Path({str(job_dir)!r}), rows), True)[1]))"
    )
    cmd = sandbox.command({**sandbox.limits(10), "fsize": 4096}, ["-c", code])
    proc = subprocess.run(cmd, capture_output=True, check=False, stdin=subprocess.DEVNULL, timeout=60)
    assert classify(proc.returncode, proc.stdout) == "too_large_output", proc.stderr
    assert list((job_dir / "work").glob("*.pdf")) == [] and not list(job_dir.glob("*.tmp"))
    assert out.read_bytes() == b"previous"


def test_runner_fails_a_cut_whose_zip_hits_rlimit_fsize_as_too_large_output(
    settings: Settings, wstore: Store, analyzed_template: Path
) -> None:
    """Through the real runner and task, `write_zip` forced to fail with EFBIG inside the sandboxed process
    (the sections themselves fit): `failed/too_large_output` with its message, `work/` empty, no `.tmp`, the
    previous `result.zip` kept — and the job re-cuts to `done` from `failed` like any other failed cut."""
    job_dir = seed_job(settings, analyzed_template, DASH_ID, state="queued", kind="cut")
    (job_dir / "result.zip").write_bytes(b"previous")
    forced = (
        "import errno, sys\n"
        "from pdf_splitter.worker import cut, task\n"
        "def refuse(*a, **kw): raise OSError(errno.EFBIG, 'File too large')\n"
        "cut.write_zip = refuse\n"
        "sys.exit(task.main(sys.argv[1:]))\n"
    )
    runner = Runner(settings, task=lambda kind, job_id: ["-c", forced, kind, "--", job_id], kinds=("cut",))
    assert runner.run_once(wstore) is True
    job = wstore.get_job(DASH_ID)
    assert (job["state"], job["kind"], job["error_code"]) == ("failed", "cut", "too_large_output"), job
    assert job["message"] == runner_mod.MESSAGES["too_large_output"]
    assert (job_dir / "result.zip").read_bytes() == b"previous" and not list(job_dir.glob("*.tmp"))
    assert list((job_dir / "work").glob("*.pdf")) == []
    assert wstore.transition(DASH_ID, "failed", "queued", kind="cut", expect_kind="cut")
    assert Runner(settings, kinds=("cut",)).run_once(wstore) is True
    assert (wstore.get_job(DASH_ID)["state"], wstore.get_job(DASH_ID)["error_code"]) == ("done", None)
    assert zipfile.ZipFile(job_dir / "result.zip").namelist()[-1] == cut.MANIFEST


def test_hit_file_limit_tells_the_sandboxs_efbig_from_an_allocator_failure() -> None:
    """Gate r2 finding 2: MuPDF's section write under RLIMIT_FSIZE is `FzErrorSystem('code=2: cannot fwrite:
    File too large')` — the same `code=2` prefix as its allocator failing, so only the errno text can tell them
    apart; the allocator's stays `resources` (`task.is_mupdf_alloc_failure`)."""
    assert hit_file_limit(OSError(errno.EFBIG, "File too large"))
    assert not hit_file_limit(OSError(errno.EIO, "Input/output error"))
    assert hit_file_limit(pymupdf.mupdf.FzErrorSystem("cannot fwrite: File too large"))
    assert hit_file_limit(RuntimeError("code=2: cannot fwrite: File too large"))
    assert not hit_file_limit(RuntimeError("code=2: cannot fwrite: No space left on device"))
    assert not hit_file_limit(ValueError("File too large"))                       # Python's words, not MuPDF's
    calloc = pymupdf.mupdf.FzErrorSystem("calloc (4104 x 1 bytes) failed")
    assert not hit_file_limit(calloc) and task.is_mupdf_alloc_failure(calloc)


def ranges_plan_of(plan: dict, pages: int) -> dict:
    """The chapter plan's sections as whole-page spans to the end of the book (ADR-009's shape)."""
    spans = [{"name": s["name"], "page": s["page"], "endPage": pages} for s in plan["sections"]]
    return {**plan, "source": "ranges", "sections": spans, "overrides": {}}


@pytest.mark.parametrize("path", ["chapters", "ranges"])
def test_a_section_the_sandbox_refuses_is_too_large_output_on_both_paths(
    settings: Settings, analyzed_template: Path, path: str
) -> None:
    """Gate r2 finding 2, under the real sandbox with an fsize below one section: PyMuPDF writes the section
    through MuPDF's own fwrite, so the EFBIG arrives as a MuPDF error, not an `OSError`, and read as
    `resources` with the truncated section left in `work/`. On the engine path and the page-range path alike
    it is the output being too large: `too_large_output`, no section left, the index cache untouched."""
    job_dir = seed_job(settings, analyzed_template, DASH_ID)
    work = job_dir / "work"
    plan = read_json(job_dir / "plan.json")
    fn = cut_book
    if path == "ranges":
        plan, fn = ranges_plan_of(plan, 6), cut_ranges
    # The limit sits under the smallest section as it is on this build, so a fixture that grows can't pass by size.
    rows, _ = fn(job_dir, plan, lambda *_: None)
    smallest = min((work / f"{r['formula']}.pdf").stat().st_size for r in rows)
    assert len(rows) == 3 and smallest > 8192
    index_before = (work / ".book-index.json").read_bytes()
    code = (
        GUARDED + f"import json; from pathlib import Path; from pdf_splitter.worker.cut import {fn.__name__}; "
        f"plan = json.loads({json.dumps(plan)!r}); "
        f"sys.exit(guarded(lambda: ({fn.__name__}(Path({str(job_dir)!r}), plan, lambda *_: None), True)[1]))"
    )
    cmd = sandbox.command({**sandbox.limits(10), "fsize": smallest // 2}, ["-c", code])
    proc = subprocess.run(cmd, capture_output=True, check=False, stdin=subprocess.DEVNULL, timeout=60)
    assert classify(proc.returncode, proc.stdout) == "too_large_output", proc.stderr
    assert list(work.glob("*.pdf")) == [] and not (work / MANIFEST_NAME).exists()
    assert (work / ".book-index.json").read_bytes() == index_before


def test_runner_fails_a_cut_whose_section_hits_rlimit_fsize_as_too_large_output(
    settings: Settings, wstore: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same EFBIG through the real runner and task (the row plumbing is the same on both paths, so one —
    page ranges — stands for it): the sandbox's fsize lowered to 128 KiB, well over the task's own writes on the
    way (the row updates append to the WAL that the fixtures already filled, ~40 KB) and well under one
    ~500 KB whole-book span. The row ends `failed/cut/too_large_output` with its message, `work/` holds no
    section, the previous `result.zip` is kept — then, under the real limits, the job re-cuts to `done`."""
    job_dir = settings.jobs_dir / DASH_ID
    job_dir.mkdir(parents=True)
    text_book(job_dir / "source.pdf", 120)
    write_json(job_dir / "plan.json", {
        "source": "ranges", "settings": DEFAULT_SETTINGS, "overrides": {},
        "sections": [{"name": "Whole", "page": 1, "endPage": 120}, {"name": "Again", "page": 1, "endPage": 120}],
    })
    (job_dir / "result.zip").write_bytes(b"previous")
    wstore.create_job(job_id=DASH_ID, ip_hash="h", filename="b.pdf", bytes=(job_dir / "source.pdf").stat().st_size,
                      pages=120, ttl_hours=24, state="queued", kind="cut")
    real = sandbox.limits
    monkeypatch.setattr(sandbox, "limits", lambda timeout: {**real(timeout), "fsize": 128 * 1024})
    assert Runner(settings, kinds=("cut",)).run_once(wstore) is True
    job = wstore.get_job(DASH_ID)
    assert (job["state"], job["kind"], job["error_code"]) == ("failed", "cut", "too_large_output"), job
    assert job["message"] == runner_mod.MESSAGES["too_large_output"]
    assert list((job_dir / "work").glob("*.pdf")) == [] and not list(job_dir.glob("*.tmp"))
    assert (job_dir / "result.zip").read_bytes() == b"previous"
    monkeypatch.setattr(sandbox, "limits", real)
    assert wstore.transition(DASH_ID, "failed", "queued", kind="cut", expect_kind="cut")
    assert Runner(settings, kinds=("cut",)).run_once(wstore) is True
    assert (wstore.get_job(DASH_ID)["state"], wstore.get_job(DASH_ID)["error_code"]) == ("done", None)
    assert zipfile.ZipFile(job_dir / "result.zip").namelist()[-1] == cut.MANIFEST


# ── run_cut / the task / the runner ──────────────────────────────────────────────────────────────────


@pytest.fixture
def wstore(settings: Settings):
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    s = Store(settings.db_path)
    s.init()
    yield s
    s.close()


@pytest.mark.parametrize(("state", "kind"), [("queued", "cut"), ("review", "analyze"), ("running", "analyze")])
def test_run_cut_skips_a_job_that_is_not_a_running_cut(
    settings: Settings, wstore: Store, analyzed_template: Path, state: str, kind: str
) -> None:
    job_dir = seed_job(settings, analyzed_template, DASH_ID, state=state, kind=kind)
    assert task.run_cut(settings, DASH_ID, wstore) is False
    assert not (job_dir / "result.zip").exists()
    assert wstore.get_job(DASH_ID)["state"] == state


def test_run_cut_does_not_recreate_a_job_deleted_while_the_engine_ran(
    settings: Settings, wstore: Store, analyzed_template: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_dir = seed_job(settings, analyzed_template, DASH_ID, state="running", kind="cut")
    real = task.cut_book

    def delete_then_cut(*a, **kw):
        rows = real(*a, **kw)
        wstore.set_state(DASH_ID, "deleted")            # DELETE /api/jobs/{id} lands mid-cut
        return rows

    monkeypatch.setattr(task, "cut_book", delete_then_cut)
    assert task.run_cut(settings, DASH_ID, wstore) is False
    assert not (job_dir / "result.zip").exists() and not list(job_dir.glob("*.tmp"))
    assert wstore.get_job(DASH_ID)["state"] == "deleted"


def test_task_main_runs_a_cut(
    settings: Settings, wstore: Store, analyzed_template: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    job_dir = seed_job(settings, analyzed_template, DASH_ID, state="queued", kind="cut")
    wstore.claim_next("cut")
    monkeypatch.setenv("PDFSPLIT_JOBS_DIR", str(settings.jobs_dir))
    assert task.main(["cut", "--", DASH_ID]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {"ok": True}
    assert (job_dir / "result.zip").exists()
    assert wstore.get_job(DASH_ID)["state"] == "done"


def test_runner_runs_the_real_cut_task_to_done(
    settings: Settings, wstore: Store, analyzed_template: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The kickoff's AC-4 contract: `Runner(kinds=("analyze", "cut"))` claims a queued cut and the sandboxed
    task writes `result.zip` = `NNN - <name>.pdf` × 3 + manifest.json, then moves the row to `done`."""
    caplog.set_level(logging.INFO)
    job_dir = seed_job(settings, analyzed_template, DASH_ID, state="queued", kind="cut")
    plan = read_json(job_dir / "plan.json")
    assert Runner(settings, kinds=("analyze", "cut")).run_once(wstore) is True
    job = wstore.get_job(DASH_ID)
    assert (job["state"], job["kind"], job["error_code"]) == ("done", "cut", None), job
    assert (job["progress"], job["total"], job["message"]) == (3, 3, None)     # finished: no phase, like review
    with zipfile.ZipFile(job_dir / "result.zip") as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read(cut.MANIFEST))
        assert all(zf.read(n).startswith(b"%PDF-") for n in names if n.endswith(".pdf"))
    assert names == [zip_entry(i, s["name"]) for i, s in enumerate(plan["sections"])] + [cut.MANIFEST]
    assert [(m["index"], m["name"], m["file"]) for m in manifest] == [
        (i, s["name"], zip_entry(i, s["name"])) for i, s in enumerate(plan["sections"])
    ]
    assert all(m["leaks"] == [] and m["pageCount"] >= 1 and m["bytes"] > 0 for m in manifest)
    assert not list(job_dir.glob("*.tmp"))
    assert f"job {log_id(DASH_ID)} cut finished" in caplog.text
    assert_id_gone(caplog.text, DASH_ID)
    assert Runner(settings, kinds=("analyze", "cut")).run_once(wstore) is False


def test_real_cut_task_on_a_link_uri_bomb_is_resources(
    settings: Settings, wstore: Store, caplog: pytest.LogCaptureFixture
) -> None:
    """The cut inherits the analyze's failure mapping: MuPDF's allocator failing inside `Book.open`'s index
    pass (a raw-binding `FzErrorSystem`) reads as `resources`, not `internal`. ≈ 2 s."""
    caplog.set_level(logging.WARNING)
    job_dir = settings.jobs_dir / DASH_ID
    job_dir.mkdir(parents=True)
    link_uri_bomb(job_dir / "source.pdf")
    write_json(job_dir / "plan.json", {
        "source": "manual", "settings": {"column_split": 0.487, "single_column": False, "header_band": 50.0,
                                         "footer_band": 32.0, "heading_min_size": 12.5},
        "sections": [{"name": "One", "page": 1, "heading": ""}], "overrides": {},
    })
    wstore.create_job(job_id=DASH_ID, ip_hash="h", filename="b.pdf", bytes=1, pages=2, ttl_hours=24,
                      state="queued", kind="cut")
    assert Runner(settings, kinds=("cut",)).run_once(wstore) is True
    job = wstore.get_job(DASH_ID)
    assert (job["state"], job["error_code"], job["message"]) == (
        "failed", "resources", runner_mod.MESSAGES["resources"]
    )
    assert not (job_dir / "result.zip").exists() and not list(job_dir.glob("*.tmp"))
    assert "cut failed: resources" in caplog.text
    assert_id_gone(caplog.text, DASH_ID)
