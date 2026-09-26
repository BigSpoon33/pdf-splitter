"""The cut job's engine calls: the saved Plan → one PDF per section in `<id>/work/`, then `<id>/result.zip`.

The engine never sees a display name. Each section goes in as `NNN-<ascii-slug>` (unique and filename-safe,
so a name with `/` or one that repeats can't be refused or collide), overrides are re-keyed from the Plan's
section index to that name, and the ZIP carries `NNN - <display name>.pdf` plus a `manifest.json` whose rows
are the engine's with the Plan's `index`, `name` and the ZIP `file`. The task (`worker/task.py`) owns the row.

A `ranges` plan (ADR-009) never reaches the engine: `cut_ranges` copies each whole-page span with PyMuPDF into
the same file names and manifest shape, so the ZIP, the manifest route and the SPA cannot tell the two apart.

Both paths write under an output budget (STORY-012): the bytes in `work/` are measured after every section and
the cut stops with `OutputTooLarge` — its files removed, the previous `result.zip` untouched — the moment they
pass it. Spans may overlap and sections may be the whole book, so nothing else bounds what a plan can write.
The budget sits under the sandbox's RLIMIT_FSIZE with room for the ZIP, and a write the sandbox refuses anyway
(EFBIG — from Python as an `OSError`, from MuPDF's own `fwrite` as a MuPDF error that only quotes the errno text)
is the same failure to the visitor, cleaned up the same way.
"""

from __future__ import annotations

import errno
import json
import os
import re
import unicodedata
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pymupdf
from monograph_splitter.profile import profile_from_dict
from monograph_splitter.session import MANIFEST_NAME, OVERRIDES_NAME, Book

from .analyze import MUPDF_ERRORS, Progress
from .sandbox import FSIZE_BYTES

ENGINE_NAME_BYTES = 80
ZIP_NAME_CHARS = 120
MANIFEST = "manifest.json"
MSG_PREPARING = "Preparing the book"
MSG_CUTTING = "Cutting sections"
MSG_PACKAGING = "Packaging the sections"
# The budget is ~10× the upload, never under the floor (a small book whose every section re-embeds its fonts is
# honest work) and never over `Settings.max_output_bytes` or the ceiling: the ZIP of the sections must still fit
# the task's RLIMIT_FSIZE (one file), and it is the sections stored plus the manifest and the entry headers.
OUTPUT_MULTIPLIER = 10
OUTPUT_FLOOR = 256 * 1024 * 1024
ZIP_MARGIN = 64 * 1024 * 1024
OUTPUT_CEILING = FSIZE_BYTES - ZIP_MARGIN
# MuPDF saves through its own fwrite and raises the C error as text (`code=2: cannot fwrite: File too large`), so
# an EFBIG from a section write carries no errno — only libc's words for it, the same libc the sandbox runs on.
MUPDF_FILE_TOO_LARGE = re.compile(rf"\b{re.escape(os.strerror(errno.EFBIG))}\b|\bEFBIG\b", re.IGNORECASE)


class OutputTooLarge(Exception):
    """The cut passed its byte budget; `task.guarded` reports it as `too_large_output`."""


def hit_file_limit(e: BaseException) -> bool:
    """A write the sandbox's RLIMIT_FSIZE refused, whichever side raised it: Python's `OSError(EFBIG)`, or a MuPDF
    error whose message is the same errno — `code=2` alone is also what MuPDF's allocator says when it fails, so
    the type and the prefix decide nothing here, only the errno text does."""
    if isinstance(e, OSError):
        return e.errno == errno.EFBIG
    return isinstance(e, MUPDF_ERRORS) and MUPDF_FILE_TOO_LARGE.search(str(e)) is not None


def output_budget(max_output_bytes: int, upload_bytes: int) -> int:
    return min(max_output_bytes, OUTPUT_CEILING, max(OUTPUT_MULTIPLIER * upload_bytes, OUTPUT_FLOOR))


def written_bytes(work: Path) -> int:
    """What the sections written so far take on disk, whichever path wrote them."""
    return sum(p.stat().st_size for p in work.glob("*.pdf"))


class Budget:
    """Checked after every section: the sizes are summed from `work/` itself, so a section written by the engine
    counts the same as one copied by PyMuPDF, and so does anything a stale file left there."""

    def __init__(self, work: Path, limit: int | None) -> None:
        self.work, self.limit = work, limit

    def check(self) -> None:
        if self.limit is not None and written_bytes(self.work) > self.limit:
            raise OutputTooLarge(self.limit)

_NOT_SLUG = re.compile(r"[^a-z0-9]+")
# `/` and `\` are separators in every unzip tool; the rest are refused by Windows file names.
_NOT_FILENAME = re.compile(r'[/\\:*?"<>|]')


def _silent(*_: object) -> None:
    """The engine logs to stdout by default; the task's result must stay the last stdout line."""


def engine_name(index: int, name: str) -> str:
    slug = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    slug = _NOT_SLUG.sub("-", slug).strip("-")
    prefix = f"{index + 1:03d}-"
    return prefix + (slug[: ENGINE_NAME_BYTES - len(prefix)].rstrip("-") or "section")


def engine_entries(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"name": engine_name(i, s["name"]), "page": s["page"], "heading": s.get("heading") or ""}
        for i, s in enumerate(sections)
    ]


def zip_entry(index: int, name: str) -> str:
    """`NNN - <display name>.pdf`: the index keeps book order and makes the entry unique whatever the name."""
    clean = "".join(c for c in _NOT_FILENAME.sub("-", name) if unicodedata.category(c)[0] != "C")
    clean = " ".join(clean.split()) or "section"
    prefix = f"{index + 1:03d} - "
    return prefix + clean[: ZIP_NAME_CHARS - len(prefix) - 4].rstrip() + ".pdf"


def _reset_outputs(work: Path) -> None:
    # `Book.open` reads a manifest and overrides left by an earlier cut of this job and keeps their rows, so a
    # section dropped from the plan (or an override removed) would come back; only the index cache is reused.
    work.mkdir(parents=True, exist_ok=True)
    for stale in (work / MANIFEST_NAME, work / OVERRIDES_NAME, *work.glob("*.pdf")):
        stale.unlink(missing_ok=True)


@contextmanager
def _within_budget(work: Path) -> Iterator[None]:
    """Too much output, whether the budget said so or the sandbox did (a section or the ZIP hitting
    RLIMIT_FSIZE fails with EFBIG — Python ignores SIGXFSZ — through Python's `OSError` or MuPDF's own writer):
    the sections go, `OutputTooLarge` comes out. Anything else the sandbox refuses keeps its own code, so a MuPDF
    allocator failure still reads as `resources`."""
    try:
        yield
    except OutputTooLarge:
        _reset_outputs(work)
        raise
    except (OSError, *MUPDF_ERRORS) as e:
        if not hit_file_limit(e):
            raise
        _reset_outputs(work)
        raise OutputTooLarge(FSIZE_BYTES) from e


def cut_book(
    job_dir: Path, plan: dict[str, Any], progress: Progress, limit: int | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Cut every section of `plan`; returns (manifest rows in plan order, the engine's `cut_all` summary).
    `limit` is the output budget in bytes (None: unbounded)."""
    prof = profile_from_dict(plan["settings"])
    work = job_dir / "work"
    _reset_outputs(work)
    budget = Budget(work, limit)
    entries = engine_entries(plan["sections"])
    progress(0, len(entries), MSG_PREPARING)

    def tick(done: int, total: int, _name: str) -> None:
        # The engine fires this right after each section's file is written: the one place to measure.
        budget.check()
        progress(done, total, MSG_CUTTING)

    book = Book.open(pdf=job_dir / "source.pdf", out=work, profile=prof, entries=entries, log=_silent)
    try:
        with _within_budget(work):
            for key, override in plan.get("overrides", {}).items():
                book.set_override(entries[int(key)]["name"], override)
            result = book.cut_all(progress=tick, verify=True)
    finally:
        book.close()
    written = {row["formula"]: row for row in result["written"]}
    rows = []
    for i, section in enumerate(plan["sections"]):
        row = written.get(entries[i]["name"])
        if row is not None:
            rows.append({**row, "index": i, "name": section["name"], "file": zip_entry(i, section["name"])})
    return rows, result


def cut_ranges(
    job_dir: Path, plan: dict[str, Any], progress: Progress, limit: int | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Copy every `[page, endPage]` span of a `ranges` plan into its own PDF (no heading search, no redaction,
    no verify); returns (manifest rows in plan order, a summary in `cut_all`'s shape). Spans may overlap or
    leave gaps: each is copied from the source on its own. `limit` as in `cut_book`."""
    work = job_dir / "work"
    _reset_outputs(work)
    budget = Budget(work, limit)
    sections = plan["sections"]
    total = len(sections)
    progress(0, total, MSG_PREPARING)
    rows: list[dict[str, Any]] = []
    src = pymupdf.open(job_dir / "source.pdf")
    try:
        with _within_budget(work):
            for i, section in enumerate(sections):
                name = engine_name(i, section["name"])
                dest = work / f"{name}.pdf"
                page, end_page = section["page"], section["endPage"]
                out = pymupdf.open()
                try:
                    # 1-based inclusive sheets (ADR-003) → PyMuPDF's 0-based inclusive pair.
                    out.insert_pdf(src, from_page=page - 1, to_page=end_page - 1)
                    out.save(dest, garbage=4, deflate=True)
                finally:
                    out.close()
                budget.check()
                rows.append({
                    "formula": name,
                    "file": zip_entry(i, section["name"]),
                    "printedPages": [page, end_page],
                    "pageCount": end_page - page + 1,
                    "flags": [],
                    "notes": [],
                    "leaks": [],
                    "bytes": dest.stat().st_size,
                    "index": i,
                    "name": section["name"],
                })
                progress(i + 1, total, MSG_CUTTING)
    finally:
        src.close()
    return rows, {"written": rows, "missing": [], "unknown": [], "leaks": {}}


def write_zip(path: Path, work: Path, rows: list[dict[str, Any]]) -> None:
    """`result.zip`, replaced whole: the previous one stays downloadable until this one is complete, and a
    failure part-way (a hit RLIMIT_FSIZE, a full disk) leaves no `.tmp` behind."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w") as zf:
            for row in rows:
                # The excerpts are already deflated inside (PyMuPDF `deflate=True`); storing skips a second pass.
                zf.write(work / f"{row['formula']}.pdf", row["file"], compress_type=zipfile.ZIP_STORED)
            zf.writestr(MANIFEST, json.dumps(rows, ensure_ascii=False, indent=1), compress_type=zipfile.ZIP_DEFLATED)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def package(job_dir: Path, rows: list[dict[str, Any]]) -> None:
    """`write_zip` for the cut task: a ZIP the sandbox refuses is the cut's output being too large, not a
    resource failure — the visitor is told to split into fewer or smaller sections, and no section stays."""
    with _within_budget(job_dir / "work"):
        write_zip(job_dir / "result.zip", job_dir / "work", rows)
