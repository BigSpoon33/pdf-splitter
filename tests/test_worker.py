from __future__ import annotations

import errno
import json
import logging
import resource
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pymupdf
import pytest
from fixtures.books import headed_book, text_book
from fixtures.hostile import link_uri_bomb, outline_book, xobj_bomb
from monograph_splitter.profile import profile_from_dict

from pdf_splitter import cli, upload
from pdf_splitter.config import Settings
from pdf_splitter.store import Store, log_id
from pdf_splitter.worker import analyze as analyze_mod
from pdf_splitter.worker import runner as runner_mod
from pdf_splitter.worker import sandbox, task
from pdf_splitter.worker.analyze import (
    DEFAULT_SETTINGS,
    analyze,
    clean_text,
    default_plan,
    json_safe,
    page_labels,
    suggest,
)
from pdf_splitter.worker.runner import REQUEUED, Runner, classify, loggable_tail, task_args
from pdf_splitter.worker.task import Throttle

DASH_ID = "-bCdEfGhIjKlMnOpQrSt00"  # the token_urlsafe(16) shape with the awkward leading `-` (1 in 64)
OTHER_ID = "xYzAbCdEfGhIjKlMnOpQ01"
ANALYSIS_KEYS = {"pages", "pageLabels", "size", "outline", "headings", "suggested"}
PLAN_KEYS = {"source", "settings", "sections", "overrides"}

# Fake tasks: `python -c CODE <id>`, run through the real sandbox launcher. `guarded` is the real task's
# result/exit contract, so a fake that hits an rlimit reports it exactly like the analyze task would.
GUARDED = "import sys; from pdf_splitter.worker.task import guarded; "
FAKE_OK = (
    "import sys; from pdf_splitter.config import Settings; from pdf_splitter.store import Store; "
    "s = Store(Settings().db_path); s.transition(sys.argv[1], 'running', 'review'); print('{\"ok\": true}')"
)
FAKE_SLEEP = "import time; time.sleep(60)"
FAKE_CRASH = "import sys; raise RuntimeError('boom in /jobs/' + sys.argv[1] + '/source.pdf')"
FAKE_HOG = GUARDED + "sys.exit(guarded(lambda: bytearray(3 * 1024**3)))"
FAKE_EXIT0_NO_STATE = "print('{\"ok\": true}')"


def fake(code: str):
    return lambda kind, job_id: ["-c", code, job_id]


def assert_id_gone(out: str, job_id: str) -> None:
    """Neither the id nor any 16-char window of it survives (tests/test_upload.py's rule)."""
    assert job_id not in out
    assert not any(job_id[i : i + 16] in out for i in range(len(job_id) - 15))


def open_store(settings: Settings) -> Store:
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    s = Store(settings.db_path)
    s.init()
    return s


def queue_job(
    settings: Settings, store: Store, job_id: str, pdf: str = "headed", pages: int = 6,
    title_hex: list[str] | None = None,
) -> Path:
    job_dir = settings.jobs_dir / job_id
    job_dir.mkdir(parents=True)
    src = job_dir / "source.pdf"
    if pdf == "headed":
        headed_book(src)
    elif pdf == "text":
        text_book(src, pages)
    elif pdf == "xbomb":
        xobj_bomb(src)
    elif pdf == "linkbomb":
        link_uri_bomb(src)
    elif pdf == "outline":
        outline_book(src, title_hex or [])
    else:
        src.write_bytes(b"%PDF-1.7\n" + b"\x00garbage" * 50)
    store.create_job(job_id=job_id, ip_hash="h", filename="book.pdf", bytes=src.stat().st_size, pages=pages,
                     ttl_hours=24)
    return job_dir


@pytest.fixture
def wstore(settings: Settings):
    s = open_store(settings)
    yield s
    s.close()


# ── AC-2 / AC-6: the analysis and the default plan (in process) ───────────────────────────────────────────


def test_analyze_outline_book(tmp_path: Path) -> None:
    book = headed_book(tmp_path / "b.pdf")
    a = analyze(tmp_path / "b.pdf", tmp_path / "work")
    assert set(a) == ANALYSIS_KEYS
    assert a["pages"] == 6
    assert a["pageLabels"] == [""] * 6
    assert len(a["size"]) == 6 and a["size"][0] == {"W": 522.7, "H": 789.6}
    assert a["outline"]["levels"] == [3, 3]
    items = a["outline"]["items"]
    assert [it["level"] for it in items] == [1, 1, 1, 2, 2, 2]
    assert [it["heading"] for it in items if it["level"] == 1] == [c["name"] for c in book["chapters"]]
    assert all(set(it) <= {"name", "page", "heading", "level", "y"} for it in items)
    assert set(a["headings"]) == {"body_size", "levels", "candidates"}
    assert a["headings"]["levels"] == [{"size": 16.0, "count": 3}]
    assert a["suggested"] == {"source": "outline", "level": 1}
    # The index is cached where Book.open(out=work) will look for it.
    assert (tmp_path / "work" / ".book-index.json").exists()


def test_analyze_headings_book_without_outline(tmp_path: Path) -> None:
    book = headed_book(tmp_path / "b.pdf", outline=False)
    a = analyze(tmp_path / "b.pdf", tmp_path / "work")
    assert a["outline"] == {"levels": [], "items": []}
    cands = a["headings"]["candidates"]
    assert [(c["name"], c["page"]) for c in cands] == [(c["name"], c["page"]) for c in book["chapters"]]
    assert all(set(c) == {"name", "page", "heading", "size", "level", "y", "col"} for c in cands)
    assert a["suggested"] == {"source": "headings", "level": 1}


@pytest.mark.parametrize(
    ("outline_levels", "heading_counts", "expected"),
    [
        ([2, 9], [40], {"source": "outline", "level": 1}),
        ([1, 9], [40], {"source": "headings", "level": 1}),     # a lone level-1 item (the book title)
        ([], [3, 100], {"source": "headings", "level": 1}),     # 2 short of 5 beats 40 over 60
        ([], [100, 30], {"source": "headings", "level": 2}),    # inside 5–60
        ([], [70, 3], {"source": "headings", "level": 2}),      # 2 short beats 10 over
        ([], [4, 61], {"source": "headings", "level": 1}),      # a tie goes to the bigger type
        ([0, 12], [], {"source": "manual", "level": None}),
        ([], [], {"source": "manual", "level": None}),
    ],
)
def test_suggest(outline_levels: list[int], heading_counts: list[int], expected: dict) -> None:
    analysis = {
        "outline": {"levels": outline_levels, "items": []},
        "headings": {"levels": [{"size": 30.0 - i, "count": n} for i, n in enumerate(heading_counts)]},
    }
    assert suggest(analysis) == expected


def test_default_plan_from_outline_and_headings(tmp_path: Path) -> None:
    for outline, want in ((True, "outline"), (False, "headings")):
        book = headed_book(tmp_path / f"{outline}.pdf", outline=outline)
        plan = default_plan(analyze(tmp_path / f"{outline}.pdf", tmp_path / f"work{outline}"))
        assert set(plan) == PLAN_KEYS
        assert plan["source"] == want
        assert [s["page"] for s in plan["sections"]] == [c["page"] for c in book["chapters"]]
        assert all(set(s) == {"name", "page", "heading"} for s in plan["sections"])
        assert plan["overrides"] == {}


def test_default_plan_settings_round_trip_and_match_the_index_profile() -> None:
    plan = default_plan({"suggested": {"source": "manual", "level": None}, "outline": {}, "headings": {}})
    assert plan["sections"] == []
    assert set(plan["settings"]) == {
        "column_split", "single_column", "header_band", "footer_band", "heading_min_size"
    }
    # Only WEB_KEYS, and the same profile hash as the engine defaults: the analyze index cache serves it.
    assert profile_from_dict(plan["settings"]).sha256 == profile_from_dict({}).sha256
    assert plan["settings"] is not DEFAULT_SETTINGS


def test_page_labels(tmp_path: Path) -> None:
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    doc.set_page_labels([{"startpage": 0, "prefix": "A-", "style": "r", "firstpagenum": 1}])
    assert page_labels(doc) == ["A-i", "A-ii", "A-iii"]
    # A label tree PyMuPDF can't parse (a real book has one) costs the labels, not the analysis.
    xref = doc.get_new_xref()
    doc.update_object(xref, "<</Nums[0<</S/D/Stx 1>>]>>")
    doc.xref_set_key(doc.pdf_catalog(), "PageLabels", f"{xref} 0 R")
    assert page_labels(doc) == ["", "", ""]


def test_page_labels_survive_a_raw_mupdf_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate r2: the raw bindings raise `FzError*` (not RuntimeError) when MuPDF refuses the label tree."""
    doc = pymupdf.open()
    doc.new_page()
    doc.set_page_labels([{"startpage": 0, "prefix": "A-", "style": "r", "firstpagenum": 1}])

    def refuse(*_: object) -> None:
        raise pymupdf.mupdf.FzErrorFormat("cannot parse page label tree")

    monkeypatch.setattr(pymupdf.Page, "get_label", refuse)
    assert page_labels(doc) == [""]


def test_json_safe_replaces_every_lone_surrogate_and_nothing_else() -> None:
    # `surrogateescape` bytes (PyMuPDF's decoding of a bad text string), a lone high, a lone low, and a pair
    # that is NOT a valid pair in a str either (each code unit is still a lone surrogate to UTF-8).
    assert clean_text("Chapt\udcffer") == "Chapt�er"
    assert clean_text("Ch\udced\udca0\udc80") == "Ch���"
    assert clean_text("\ud800x\udc00") == "�x�"
    assert clean_text("Zhōngyī 中医 – Kapitel 1 ✓ 😀") == "Zhōngyī 中医 – Kapitel 1 ✓ 😀"
    nested = {"a\udcff": [("b\ud800", 1.5, None, True), {"c": "d\udfff"}], "n": 3}
    cleaned = json_safe(nested)
    assert cleaned == {"a�": [["b�", 1.5, None, True], {"c": "d�"}], "n": 3}
    json.dumps(cleaned, ensure_ascii=False).encode("utf-8")        # strict: nothing left to reject
    with pytest.raises(UnicodeEncodeError):
        json.dumps(nested, ensure_ascii=False).encode("utf-8")


# ── AC-3: throttled progress ────────────────────────────────────────────────────────────────────────────


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def test_throttle_writes_at_most_every_half_second() -> None:
    clock, writes = FakeClock(), []
    th = Throttle(lambda d, t, m: writes.append((clock.t, d, m)), clock=clock, sleep=clock.sleep)
    for i in range(1, 101):
        th(i, 100, "Indexing pages")
        clock.t += 0.03                       # 100 pages in 3 s
    th(100, 100, "Finding headings")        # a phase change within the window waits it out, never drops
    times = [w[0] for w in writes]
    assert all(b - a >= 0.5 - 1e-9 for a, b in pairwise(times))
    assert writes[0][1] == 1 and writes[-1][1:] == (100, "Finding headings")
    assert 6 <= len(writes) <= 8
    assert clock.slept and all(0 < s <= 0.5 for s in clock.slept)


def test_task_progress_reaches_the_row_throttled(
    settings: Settings, wstore: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue_job(settings, wstore, DASH_ID, pdf="text", pages=40)
    wstore.claim_next("analyze")
    stamps: list[tuple[float, int, int, str | None]] = []
    real = Store.update_progress

    def spy(self, job_id, progress, total, message=None, now=None):
        stamps.append((time.monotonic(), progress, total, message))
        return real(self, job_id, progress, total, message, now)

    monkeypatch.setattr(Store, "update_progress", spy)
    # Slow the engine's per-page work so indexing spans several throttle windows.
    real_index_page = analyze_mod.index_book.__globals__["index_page"]
    monkeypatch.setitem(analyze_mod.index_book.__globals__, "index_page",
                        lambda page, prof: (time.sleep(0.05), real_index_page(page, prof))[1])
    assert task.run_analyze(settings, DASH_ID, wstore) is True
    times = [s[0] for s in stamps]
    assert all(b - a >= 0.5 - 0.01 for a, b in pairwise(times))
    indexing = [s for s in stamps if s[3] == analyze_mod.MSG_INDEXING]
    assert len(indexing) >= 3 and all(s[2] == 40 for s in stamps)
    assert [s[1] for s in indexing] == sorted(s[1] for s in indexing)
    job = wstore.get_job(DASH_ID)
    assert (job["progress"], job["total"], job["state"]) == (40, 40, "review")


# ── the task entry point (in process) ───────────────────────────────────────────────────────────────────


def test_task_main_writes_analysis_and_plan_then_review(
    settings: Settings, wstore: Store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    job_dir = queue_job(settings, wstore, DASH_ID)
    wstore.claim_next("analyze")
    monkeypatch.setenv("PDFSPLIT_JOBS_DIR", str(settings.jobs_dir))
    assert task.main(["analyze", "--", DASH_ID]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {"ok": True}
    analysis = json.loads((job_dir / "analysis.json").read_text())
    plan = json.loads((job_dir / "plan.json").read_text())
    assert set(analysis) == ANALYSIS_KEYS and set(plan) == PLAN_KEYS
    assert plan == default_plan(analysis)
    assert not list(job_dir.glob("*.tmp"))
    job = wstore.get_job(DASH_ID)
    assert (job["state"], job["error_code"], job["message"]) == ("review", None, None)
    assert (job["progress"], job["total"]) == (6, 6)


CHAPTER_2 = "FEFF004300680061007000740065007200200032"          # a well-formed UTF-16BE "Chapter 2"


@pytest.mark.parametrize(
    ("title_hex", "name"),
    [
        ("EFBBBF4368617074FF6572", "Chapt�er"),      # UTF-8 BOM, then a byte no UTF-8 sequence allows
        ("FEFF00430068D800", "Ch���"),      # UTF-16BE ending in a lone high surrogate
    ],
)
def test_task_survives_a_bookmark_title_that_is_not_valid_unicode(
    settings: Settings, wstore: Store, monkeypatch: pytest.MonkeyPatch, title_hex: str, name: str
) -> None:
    """Gate r1 finding 2: PyMuPDF hands these titles over with lone surrogates, which strict UTF-8 rejects; the
    analysis used to die on the write and leave an empty `.tmp`."""
    job_dir = queue_job(settings, wstore, DASH_ID, pdf="outline", pages=2, title_hex=[title_hex, CHAPTER_2])
    wstore.claim_next("analyze")
    monkeypatch.setenv("PDFSPLIT_JOBS_DIR", str(settings.jobs_dir))
    assert task.main(["analyze", "--", DASH_ID]) == 0
    assert wstore.get_job(DASH_ID)["state"] == "review"
    assert not list(job_dir.glob("*.tmp"))
    analysis = json.loads((job_dir / "analysis.json").read_bytes().decode("utf-8"))   # strict decode
    plan = json.loads((job_dir / "plan.json").read_bytes().decode("utf-8"))
    assert [it["name"] for it in analysis["outline"]["items"]] == [name, "Chapter 2"]
    assert analysis["suggested"] == {"source": "outline", "level": 1}
    assert [s["name"] for s in plan["sections"]] == [name, "Chapter 2"]
    assert plan == default_plan(analysis)


def test_write_json_never_fails_on_encoding(tmp_path: Path) -> None:
    out = tmp_path / "analysis.json"
    task._write_json(out, {"name": "x\udcffy"})          # a string that dodged `json_safe`
    assert json.loads(out.read_bytes().decode("utf-8")) == {"name": "x?y"}


def test_write_json_removes_the_tmp_when_the_rename_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate r2 finding 2: a failure AFTER the `.tmp` is fully written (the rename) must still leave the
    directory as it was. (The old test failed inside `json.dumps`, before any file existed.)"""
    out, tmp = tmp_path / "analysis.json", tmp_path / "analysis.json.tmp"
    out.write_text('{"previous": true}')
    seen: dict[str, bytes] = {}

    def refuse(src: str | Path, dst: str | Path) -> None:
        seen["tmp"] = Path(src).read_bytes()             # proof the failure happens with the .tmp on disk
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(task.os, "replace", refuse)
    with pytest.raises(OSError, match="Input/output"):
        task._write_json(out, {"pages": 3})
    assert seen["tmp"] == b'{"pages": 3}'
    assert not tmp.exists()
    assert json.loads(out.read_text()) == {"previous": True}


def test_write_json_removes_a_tmp_cut_short_by_rlimit_fsize(tmp_path: Path) -> None:
    """Gate r2 finding 2, the real failure: under the sandbox's RLIMIT_FSIZE the write itself fails with
    EFBIG part-way through, leaving a truncated `.tmp` that the cleanup must remove; the task reports it as
    `resources` like any other rlimit, and the previous analysis survives."""
    out, tmp = tmp_path / "analysis.json", tmp_path / "analysis.json.tmp"
    out.write_text('{"previous": true}')
    code = (
        GUARDED + "from pathlib import Path; from pdf_splitter.worker.task import _write_json; "
        f"sys.exit(guarded(lambda: (_write_json(Path({str(out)!r}), {{'pad': 'x' * 100_000}}), True)[1]))"
    )
    proc = run_sandboxed({**sandbox.limits(10), "fsize": 4096}, code)
    assert proc.returncode == task.EXIT_RESOURCES, proc.stderr
    assert classify(proc.returncode, proc.stdout) == "resources"
    assert not tmp.exists()
    assert json.loads(out.read_text()) == {"previous": True}


def test_task_main_needs_the_double_dash_for_a_dash_id() -> None:
    with pytest.raises(SystemExit):
        task.main(["analyze", DASH_ID])


@pytest.mark.parametrize("state", ["queued", "deleted", "review"])
def test_task_skips_a_job_that_is_not_running(
    settings: Settings, wstore: Store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    state: str,
) -> None:
    job_dir = queue_job(settings, wstore, DASH_ID)
    wstore.set_state(DASH_ID, state)
    monkeypatch.setenv("PDFSPLIT_JOBS_DIR", str(settings.jobs_dir))
    assert task.main(["analyze", "--", DASH_ID]) == task.EXIT_INTERNAL
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {"ok": False, "code": "internal"}
    assert not (job_dir / "analysis.json").exists()
    assert wstore.get_job(DASH_ID)["state"] == state


@pytest.mark.parametrize("bad", ["..", ".", "a/b", "../x"])
def test_task_refuses_an_id_that_is_not_a_plain_name(bad: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert task.main(["analyze", "--", bad]) == task.EXIT_INTERNAL
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["code"] == "internal"


@pytest.mark.parametrize(
    ("exc", "code", "exit_code"),
    [
        (MemoryError(), "resources", task.EXIT_RESOURCES),
        (OSError(27, "File too large"), "resources", task.EXIT_RESOURCES),   # EFBIG under RLIMIT_FSIZE
        (OSError(2, "No such file"), "internal", task.EXIT_INTERNAL),
        (RuntimeError("engine bug"), "internal", task.EXIT_INTERNAL),
        # MuPDF's allocator failing under RLIMIT_AS (gate r1 finding 1): the message PyMuPDF 1.28.2 raises
        # for the xbomb, MuPDF's other allocator wordings, and a FZ_ERROR_SYSTEM without one.
        (RuntimeError("code=2: calloc (4104 x 1 bytes) failed"), "resources", task.EXIT_RESOURCES),
        (RuntimeError("malloc of 262144 bytes failed"), "resources", task.EXIT_RESOURCES),
        (RuntimeError("realloc (16 x 8 bytes) failed"), "resources", task.EXIT_RESOURCES),
        (RuntimeError("code=2: out of memory"), "resources", task.EXIT_RESOURCES),
        (RuntimeError("Out of memory"), "resources", task.EXIT_RESOURCES),
        # Other MuPDF failures keep their class: a format error, and a message that merely mentions a failure.
        (RuntimeError("code=7: cannot recognize xref format"), "internal", task.EXIT_INTERNAL),
        (RuntimeError("code=3: load failed for object 12"), "internal", task.EXIT_INTERNAL),
        (RuntimeError("cannot open /jobs/x/source.pdf: code=2 is not a file"), "internal", task.EXIT_INTERNAL),
        (ValueError("code=2: calloc (1 x 1 bytes) failed"), "internal", task.EXIT_INTERNAL),
        # Gate r2 finding 1: the raw bindings raise the same allocator failure as `FzErrorSystem`, an
        # Exception (not RuntimeError) subclass; the message decides, so a non-allocator FzError stays internal.
        (pymupdf.mupdf.FzErrorSystem("code=2: malloc (65537 bytes) failed"), "resources", task.EXIT_RESOURCES),
        (pymupdf.mupdf.FzErrorLibrary("out of memory"), "resources", task.EXIT_RESOURCES),
        (pymupdf.mupdf.FzErrorFormat("cannot recognize xref format"), "internal", task.EXIT_INTERNAL),
        (pymupdf.mupdf.FzErrorSyntax("expected 'obj' keyword"), "internal", task.EXIT_INTERNAL),
    ],
)
def test_guarded_maps_exceptions(
    exc: BaseException, code: str, exit_code: int, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom() -> bool:
        raise exc

    assert task.guarded(boom) == exit_code
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {"ok": False, "code": code}


# ── the sandbox launcher (real subprocesses) ────────────────────────────────────────────────────────────


def run_sandboxed(lim: dict[str, int], code: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(sandbox.command(lim, ["-c", code]), capture_output=True, timeout=timeout,
                          stdin=subprocess.DEVNULL, check=False)


def test_sandbox_sets_the_three_rlimits_before_the_task_runs() -> None:
    code = ("import resource as r; "
            "print([r.getrlimit(getattr(r, 'RLIMIT_' + k)) for k in ('AS', 'CPU', 'FSIZE')])")
    proc = run_sandboxed(sandbox.limits(300), code)
    assert proc.returncode == 0, proc.stderr
    gb = 1024**3
    assert proc.stdout.decode().strip().splitlines()[-1] == str([(2 * gb, 2 * gb), (310, 310), (gb, gb)])


def test_sandbox_memory_hog_reports_resources() -> None:
    proc = run_sandboxed(sandbox.limits(300), FAKE_HOG)
    assert proc.returncode == task.EXIT_RESOURCES
    assert classify(proc.returncode, proc.stdout) == "resources"


def test_sandbox_cpu_limit_kills_a_spinning_task() -> None:
    proc = run_sandboxed({**sandbox.limits(0), "cpu": 1}, "while True: pass", timeout=20)
    assert -proc.returncode in (signal.SIGXCPU, signal.SIGKILL)
    assert classify(proc.returncode, proc.stdout) == "resources"


def test_sandbox_fsize_limit_reports_resources(tmp_path: Path) -> None:
    big = str(tmp_path / "big")
    code = GUARDED + f"sys.exit(guarded(lambda: open({big!r}, 'wb').write(b'x' * 2_000_000)))"
    proc = run_sandboxed({**sandbox.limits(10), "fsize": 1_000_000}, code)
    assert proc.returncode == task.EXIT_RESOURCES
    assert classify(proc.returncode, proc.stdout) == "resources"


def test_sandbox_refuses_to_run_without_a_command() -> None:
    assert sandbox.main(["--as", "1", "--cpu", "1", "--fsize", "1"]) == 2
    with pytest.raises(SystemExit):
        sandbox.main(["--as", "1", "--cpu", "1", "--fsize", "1", "--"])


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (0, b'warning: The `fitz` API is deprecated\n{"ok": true}\n', None),
        (0, b"", "internal"),
        (0, b'{"ok": false, "code": "internal"}', "internal"),
        (3, b'{"ok": false, "code": "resources"}', "resources"),
        (1, b"Traceback ...", "internal"),
        (-signal.SIGXCPU, b"", "resources"),
        (-signal.SIGKILL, b"", "resources"),
        (-signal.SIGXFSZ, b"", "resources"),
        (-signal.SIGSEGV, b"", "internal"),
        (1, b'{"ok": true}', "internal"),
    ],
)
def test_classify(returncode: int, stdout: bytes, expected: str | None) -> None:
    assert classify(returncode, stdout) == expected


# ── AC-1 / AC-4: the runner ─────────────────────────────────────────────────────────────────────────────


def test_runner_runs_the_real_analyze_task_to_review(settings: Settings, wstore: Store) -> None:
    job_dir = queue_job(settings, wstore, DASH_ID)
    assert Runner(settings).run_once(wstore) is True
    job = wstore.get_job(DASH_ID)
    assert (job["state"], job["error_code"]) == ("review", None), job
    assert set(json.loads((job_dir / "analysis.json").read_text())) == ANALYSIS_KEYS
    assert set(json.loads((job_dir / "plan.json").read_text())) == PLAN_KEYS
    assert (job_dir / "work" / ".book-index.json").exists()
    assert Runner(settings).run_once(wstore) is False    # queue empty


def test_runner_command_limits_and_wall_timeout(
    settings: Settings, wstore: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}
    real_popen = subprocess.Popen

    class Spy(real_popen):
        def __init__(self, cmd, **kw):
            seen.update(cmd=cmd, **kw)
            super().__init__(cmd, **kw)

        def communicate(self, *a, **kw):
            seen.setdefault("timeout", kw.get("timeout"))
            return super().communicate(*a, **kw)

    monkeypatch.setattr(runner_mod.subprocess, "Popen", Spy)
    settings = settings.model_copy(update={"analyze_timeout": 123})
    queue_job(settings, wstore, DASH_ID)
    Runner(settings).run_once(wstore)
    cmd = seen["cmd"]
    assert cmd[:3] == [sys.executable, "-m", "pdf_splitter.worker.sandbox"]
    assert cmd[3:10] == ["--as", str(2 * 1024**3), "--cpu", "133", "--fsize", str(1024**3), "--"]
    # What the launcher execs is exactly AC-1's command, with `--` before the (dash-leading) id.
    assert cmd[10:] == ["-m", "pdf_splitter.task", "analyze", "--", DASH_ID] == task_args("analyze", DASH_ID)
    assert seen["timeout"] == 123
    assert seen["stdin"] == subprocess.DEVNULL and seen["start_new_session"] is True
    assert seen["env"]["PDFSPLIT_JOBS_DIR"] == str(settings.jobs_dir)
    assert wstore.get_job(DASH_ID)["state"] == "review"


def test_runner_wall_timeout_fails_the_job_with_timeout(settings: Settings, wstore: Store) -> None:
    settings = settings.model_copy(update={"analyze_timeout": 1})
    queue_job(settings, wstore, DASH_ID)
    t0 = time.monotonic()
    assert Runner(settings, task=fake(FAKE_SLEEP)).run_once(wstore)
    assert time.monotonic() - t0 < 10
    job = wstore.get_job(DASH_ID)
    assert (job["state"], job["error_code"]) == ("failed", "timeout")
    assert job["message"] == runner_mod.MESSAGES["timeout"]


def test_runner_timeout_kills_the_whole_process_group(
    settings: Settings, wstore: Store, tmp_path: Path
) -> None:
    settings = settings.model_copy(update={"analyze_timeout": 1})
    queue_job(settings, wstore, DASH_ID)
    marker = tmp_path / "grandchild-alive"
    grandchild = f"import time; time.sleep(3); open({str(marker)!r}, 'w')"
    code = (f"import subprocess as s, sys, time; s.Popen([sys.executable, '-c', {grandchild!r}]); "
            "time.sleep(9)")
    Runner(settings, task=fake(code)).run_once(wstore)
    time.sleep(3.5)
    assert not marker.exists()


@pytest.mark.parametrize(
    ("code", "error_code"),
    [
        (FAKE_HOG, "resources"),
        ("import os, signal; os.kill(os.getpid(), signal.SIGXCPU)", "resources"),
        ("import os, signal; os.kill(os.getpid(), signal.SIGKILL)", "resources"),
        (FAKE_CRASH, "internal"),
        ("import os; os.abort()", "internal"),
        (FAKE_EXIT0_NO_STATE, "internal"),      # a zero exit that left the row running is a task bug
    ],
)
def test_runner_failure_mapping(settings: Settings, wstore: Store, code: str, error_code: str) -> None:
    queue_job(settings, wstore, DASH_ID)
    assert Runner(settings, task=fake(code)).execute(wstore, wstore.claim_next("analyze")) == error_code
    job = wstore.get_job(DASH_ID)
    expected = ("failed", error_code, runner_mod.MESSAGES[error_code])
    assert (job["state"], job["error_code"], job["message"]) == expected


def test_real_task_on_an_unreadable_pdf_is_internal(settings: Settings, wstore: Store) -> None:
    queue_job(settings, wstore, DASH_ID, pdf="garbage")
    Runner(settings).run_once(wstore)
    assert (wstore.get_job(DASH_ID)["state"], wstore.get_job(DASH_ID)["error_code"]) == ("failed", "internal")


def test_real_task_on_a_mupdf_memory_bomb_is_resources(
    settings: Settings, wstore: Store, caplog: pytest.LogCaptureFixture
) -> None:
    """Gate r1 finding 1, with the reviewer's bomb under the real sandbox: a 4.9 KB PDF whose page 2 expands to
    10^6 glyph runs, so MuPDF's own allocator (not Python's) hits RLIMIT_AS. ≈ 8 s and 2 GB of RSS."""
    caplog.set_level(logging.WARNING)
    job_dir = queue_job(settings, wstore, DASH_ID, pdf="xbomb", pages=2)
    assert job_dir.joinpath("source.pdf").stat().st_size < 6_000
    assert Runner(settings).run_once(wstore) is True
    job = wstore.get_job(DASH_ID)
    assert (job["state"], job["error_code"], job["message"]) == (
        "failed", "resources", runner_mod.MESSAGES["resources"]
    )
    assert not (job_dir / "analysis.json").exists() and not list(job_dir.glob("*.tmp"))
    assert "failed: resources" in caplog.text
    assert_id_gone(caplog.text, DASH_ID)


def test_real_task_on_a_link_uri_bomb_is_resources(
    settings: Settings, wstore: Store, caplog: pytest.LogCaptureFixture
) -> None:
    """Gate r2 finding 1, with the reviewer's second bomb under the real sandbox: 40k links sharing a 64 KB URI
    make MuPDF's allocator fail inside `fz_load_page`, a raw binding, which raises `FzErrorSystem` rather than
    the RuntimeError the xbomb's text extraction raises. ≈ 2 s."""
    caplog.set_level(logging.WARNING)
    job_dir = queue_job(settings, wstore, DASH_ID, pdf="linkbomb", pages=2)
    assert job_dir.joinpath("source.pdf").stat().st_size < 320_000
    assert Runner(settings).run_once(wstore) is True
    job = wstore.get_job(DASH_ID)
    assert (job["state"], job["error_code"], job["message"]) == (
        "failed", "resources", runner_mod.MESSAGES["resources"]
    )
    assert not (job_dir / "analysis.json").exists() and not list(job_dir.glob("*.tmp"))
    assert "failed: resources" in caplog.text
    assert_id_gone(caplog.text, DASH_ID)


def test_runner_never_resurrects_a_job_deleted_while_running(settings: Settings, wstore: Store) -> None:
    queue_job(settings, wstore, DASH_ID)
    job = wstore.claim_next("analyze")
    code = ("import sys; from pdf_splitter.config import Settings; from pdf_splitter.store import Store; "
            "Store(Settings().db_path).set_state(sys.argv[1], 'deleted'); raise SystemExit(1)")
    Runner(settings, task=fake(code)).execute(wstore, job)
    assert wstore.get_job(DASH_ID)["state"] == "deleted"


def test_runner_survives_a_spawn_error(
    settings: Settings, wstore: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*a, **kw):
        raise OSError("fork failed")

    queue_job(settings, wstore, DASH_ID)
    monkeypatch.setattr(runner_mod.subprocess, "Popen", broken)
    assert Runner(settings).run_once(wstore) is True
    assert wstore.get_job(DASH_ID)["error_code"] == "internal"


def wait_for(pred, timeout: float = 30.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting")


def serve_in_thread(r: Runner) -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()
    t = threading.Thread(target=r.serve, args=(stop,), daemon=True)
    t.start()
    return t, stop


def test_the_loop_keeps_running_after_failed_jobs(settings: Settings, wstore: Store) -> None:
    # Long enough for the real analyze (interpreter + engine start ≈ 1.5 s), short for the sleeper.
    settings = settings.model_copy(update={"analyze_timeout": 5, "workers": 1})
    ids = {
        "crash": "cRaShAbCdEfGhIjKlMnO01", "slow": "sLoWaBcDeFgHiJkLmNoP02", "hog": "hOgAbCdEfGhIjKlMnOpQ03",
        "good": DASH_ID,
    }
    code_for = {ids["crash"]: FAKE_CRASH, ids["slow"]: FAKE_SLEEP, ids["hog"]: FAKE_HOG}
    for job_id in ids.values():
        queue_job(settings, wstore, job_id)

    def task_for(kind: str, job_id: str) -> list[str]:
        return ["-c", code_for[job_id], job_id] if job_id in code_for else task_args(kind, job_id)

    t, stop = serve_in_thread(Runner(settings, task=task_for, poll=0.05))
    try:
        wait_for(lambda: all(wstore.get_job(i)["state"] in ("review", "failed") for i in ids.values()))
    finally:
        stop.set()
        t.join(10)
    got = {name: (wstore.get_job(i)["state"], wstore.get_job(i)["error_code"]) for name, i in ids.items()}
    assert got == {"crash": ("failed", "internal"), "slow": ("failed", "timeout"),
                   "hog": ("failed", "resources"), "good": ("review", None)}
    assert not t.is_alive()


def test_at_most_workers_jobs_run_at_once(settings: Settings, wstore: Store, tmp_path: Path) -> None:
    settings = settings.model_copy(update={"workers": 2})
    spans = tmp_path / "spans"
    spans.mkdir()
    code = ("import sys, time; t0 = time.time(); time.sleep(0.6); "
            f"open({str(spans)!r} + '/' + sys.argv[1], 'w').write(f'{{t0}} {{time.time()}}')")
    ids = [f"j{i}AbCdEfGhIjKlMnOpQrS{i:02d}" for i in range(5)]
    for job_id in ids:
        queue_job(settings, wstore, job_id, pdf="text", pages=1)
    t, stop = serve_in_thread(Runner(settings, task=fake(code), poll=0.05))
    try:
        wait_for(lambda: all(wstore.get_job(i)["state"] == "failed" for i in ids))
    finally:
        stop.set()
        t.join(10)
    intervals = [tuple(map(float, (spans / i).read_text().split())) for i in ids]
    peak = max(sum(1 for a, b in intervals if a <= t < b) for t, _ in intervals)
    assert peak == 2


# ── AC-5: restart recovery ──────────────────────────────────────────────────────────────────────────────


def test_restart_requeues_a_stale_running_job_once_then_fails_it(settings: Settings, wstore: Store) -> None:
    now = datetime.now(UTC)
    old = now - timedelta(seconds=settings.analyze_timeout + 5)
    young = now - timedelta(seconds=settings.analyze_timeout - 60)
    r = Runner(settings)
    queue_job(settings, wstore, DASH_ID)
    assert wstore.claim_next("analyze", now=old)["id"] == DASH_ID
    queue_job(settings, wstore, OTHER_ID)
    assert wstore.claim_next("analyze", now=young)["id"] == OTHER_ID
    r.recover(wstore, now=now)
    job = wstore.get_job(DASH_ID)
    assert (job["state"], job["error_code"]) == ("queued", REQUEUED)
    assert wstore.get_job(OTHER_ID)["state"] == "running"          # younger than its timeout: left alone
    # The re-queued job is claimed again (the marker survives the claim) and goes stale a second time.
    assert wstore.claim_next("analyze", now=old)["error_code"] == REQUEUED
    r.recover(wstore, now=now)
    job = wstore.get_job(DASH_ID)
    assert (job["state"], job["error_code"]) == ("failed", "timeout")
    r.recover(wstore, now=now)                                     # idempotent
    assert wstore.get_job(DASH_ID)["state"] == "failed"


def test_a_requeued_job_that_succeeds_clears_the_marker(settings: Settings, wstore: Store) -> None:
    queue_job(settings, wstore, DASH_ID)
    old = datetime.now(UTC) - timedelta(seconds=settings.analyze_timeout + 5)
    wstore.claim_next("analyze", now=old)
    Runner(settings).recover(wstore)
    assert Runner(settings).run_once(wstore)
    job = wstore.get_job(DASH_ID)
    assert (job["state"], job["error_code"]) == ("review", None)


def test_serve_runs_the_sweep_on_start(settings: Settings, wstore: Store) -> None:
    queue_job(settings, wstore, DASH_ID)
    wstore.claim_next("analyze", now=datetime.now(UTC) - timedelta(seconds=settings.analyze_timeout + 5))
    t, stop = serve_in_thread(Runner(settings, poll=0.05))
    try:
        # Re-queued by the start sweep, then claimed and analyzed by the loop.
        wait_for(lambda: wstore.get_job(DASH_ID)["state"] == "review")
    finally:
        stop.set()
        t.join(10)


def test_the_sweep_skips_this_workers_own_jobs(settings: Settings, wstore: Store) -> None:
    queue_job(settings, wstore, DASH_ID)
    wstore.claim_next("analyze", now=datetime.now(UTC) - timedelta(seconds=settings.analyze_timeout + 5))
    r = Runner(settings)
    r._in_flight.add(DASH_ID)
    r.recover(wstore)
    assert wstore.get_job(DASH_ID)["state"] == "running"


# ── ADR-007: job ids never reach the log ────────────────────────────────────────────────────────────────


def test_worker_logs_never_contain_a_job_id(
    settings: Settings, wstore: Store, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    queue_job(settings, wstore, DASH_ID)
    queue_job(settings, wstore, OTHER_ID)
    # The crash's traceback names the job's path, and the fake also echoes a glued and a truncated id.
    code = FAKE_CRASH.replace("raise", "print(sys.argv[1] + 'x', sys.argv[1][:17], file=sys.stderr); raise")
    Runner(settings, task=fake(code)).run_once(wstore)
    Runner(settings).run_once(wstore)
    Runner(settings).recover(wstore, now=datetime.now(UTC) + timedelta(days=1))
    out = caplog.text
    assert log_id(DASH_ID) in out and log_id(OTHER_ID) in out
    assert "RuntimeError" in out                      # the stderr tail IS logged, redacted
    for job_id in (DASH_ID, OTHER_ID):
        assert_id_gone(out, job_id)


def test_loggable_tail_redacts_then_escapes() -> None:
    raw = f"Traceback\n  File /jobs/{DASH_ID}/source.pdf\n\x1b[31mred {DASH_ID[:16]}".encode()
    tail = loggable_tail(raw * 50)
    assert_id_gone(tail, DASH_ID)
    assert "\x1b" not in tail and " " not in tail and tail.isascii()
    assert len(json.loads(tail)) <= runner_mod.STDERR_TAIL


# ── STORY-005 addendum: the preflight runs under the same rlimits ───────────────────────────────────────


def test_preflight_is_launched_with_the_rlimit_option(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, b'{"ok": true, "pages": 1}', b"")

    monkeypatch.setattr(upload.subprocess, "run", fake_run)
    upload.run_preflight(Path("/x/source.pdf.part"), 5, DASH_ID)
    cmd = seen["cmd"]
    assert cmd[cmd.index("--cpu-limit") + 1] == "20"            # the 10 s timeout + the 10 s grace
    assert cmd[-2:] == ["--", "/x/source.pdf.part"]


def test_preflight_runs_under_rlimits(tmp_path: Path) -> None:
    pdf = tmp_path / "-x.pdf"
    text_book(pdf, 1)
    code = ("import resource as r, sys; from pdf_splitter import preflight; "
            f"preflight.main(['--cpu-limit', '20', '--', {str(pdf)!r}]); "
            "print([r.getrlimit(x) for x in (r.RLIMIT_AS, r.RLIMIT_CPU, r.RLIMIT_FSIZE)])")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, check=True)
    lines = proc.stdout.decode().strip().splitlines()
    assert json.loads(lines[-2]) == {"ok": True, "pages": 1}
    gb = 1024**3
    assert lines[-1] == str([(2 * gb, 2 * gb), (20, 20), (gb, gb)])
    # Without the option (in-process callers, tests) nothing is limited.
    assert resource.getrlimit(resource.RLIMIT_CPU)[0] == resource.RLIM_INFINITY


# ── the CLI ─────────────────────────────────────────────────────────────────────────────────────────────


def test_cli_worker_serves_a_runner_from_env_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = {}

    class FakeRunner:
        def __init__(self, settings: Settings) -> None:
            seen["settings"] = settings

        def serve(self, stop: threading.Event) -> None:
            seen["stop"] = stop

    monkeypatch.setenv("PDFSPLIT_JOBS_DIR", str(tmp_path / "j"))
    monkeypatch.setattr(cli, "Runner", FakeRunner)
    monkeypatch.setattr(cli.signal, "signal", lambda *a: None)
    cli.main(["worker"])
    assert seen["settings"].jobs_dir == tmp_path / "j"
    assert isinstance(seen["stop"], threading.Event)
