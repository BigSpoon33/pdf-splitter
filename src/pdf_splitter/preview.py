"""Read-only engine calls for the API, one throwaway subprocess each:
`python -m pdf_splitter.preview sheet|section -- <job dir>`, the request as JSON on stdin.

`sheet`: `{"sheet": n, "dpi": d, "out": "<png path under the job dir>"}` renders 1-based sheet `n` into `out`
(replaced whole).
`section`: `{"sections": [...], "settings": {...}, "index": i, "override": {...} | null}` prints the Section
plan (Architecture § Data Types) for section `i` under those settings and that override, persisting nothing.

The LAST stdout line is `{"ok": true, ...}`, `{"ok": false, "code": "gone"}` (the job directory disappeared:
a DELETE raced with the render, nothing was written) or `{"ok": false, "code": "internal"}` (the engine's lazy
`import fitz` prints above it). The API launches this through `worker.sandbox` (the worker's rlimits) and
enforces the timeout; MuPDF parses hostile input, so none of it runs in the API process (ADR-005).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

import pymupdf
from monograph_splitter.profile import profile_from_dict
from monograph_splitter.session import Book

from .worker.cut import _silent, engine_entries

KINDS = ("sheet", "section")
GONE = "gone"


def mkdir_under(job_dir: Path, target: Path) -> None:
    """Create `target` one level at a time below `job_dir`, never the job directory itself: a DELETE that
    removed the job mid-render then raises FileNotFoundError here instead of getting its directory back
    (`mkdir(parents=True)` would recreate it)."""
    path = job_dir
    for part in target.relative_to(job_dir).parts:
        path = path / part
        path.mkdir(exist_ok=True)


def render_sheet(job_dir: Path, req: dict[str, Any]) -> None:
    out = Path(req["out"])
    with pymupdf.open(job_dir / "source.pdf", filetype="pdf") as doc:
        data = doc[req["sheet"] - 1].get_pixmap(dpi=req["dpi"]).tobytes("png")
    mkdir_under(job_dir, out.parent)
    # Two requests for the same sheet may render at once; each replaces the file whole from its own tmp.
    tmp = out.with_name(f"{out.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def plan_view(book: Book, p: dict[str, Any]) -> dict[str, Any]:
    """The engine's plan view (`review/server.py:_plan_view`) with sheets numbered as the API does (1-based,
    ADR-003): `rects` name the sheet they sit on, so the UI can draw them over `/sheets/{n}.png`."""
    in_range = book.in_range(p)
    return {
        "pages": [book.printed_of(p["sheet0"]), book.printed_of(p["sheet1"])],
        "startCut": p["startCut"], "startCol": p["startCol"],
        "endCut": p["endCut"], "endCol": p["endCol"],
        "flags": p["flags"], "notes": p["notes"],
        "rects": [[book.printed_of(p["sheet0"] + r["sheet"]), r["rect"]] for r in book.rects(p)] if in_range else [],
    }


def section_plan(job_dir: Path, req: dict[str, Any]) -> dict[str, Any]:
    prof = profile_from_dict(req["settings"])
    entries = engine_entries(req["sections"])
    # `Book.open` makes `work/` with parents and writes the index there; making it ourselves first turns a
    # job directory that is already gone into FileNotFoundError. (A DELETE landing between here and the
    # engine's own mkdir still gets `work/` back; the API sweeps that after the subprocess returns.)
    mkdir_under(job_dir, job_dir / "work")
    book = Book.open(pdf=job_dir / "source.pdf", out=job_dir / "work", profile=prof, entries=entries, log=_silent)
    try:
        entry = book.entry(entries[req["index"]]["name"])
        if entry is None:
            raise ValueError("the section has no usable start page")
        # use_saved=False: an earlier cut's overrides.json in work/ must not leak into a preview.
        return plan_view(book, book.planned(entry, req.get("override") or None, use_saved=False))
    finally:
        book.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pdf_splitter.preview")
    parser.add_argument("kind", choices=KINDS)
    parser.add_argument("job_dir", type=Path)
    args = parser.parse_args(argv)
    # MuPDF's warnings on hostile files can quote file content; nothing downstream needs them.
    pymupdf.TOOLS.mupdf_display_errors(False)
    try:
        req = json.load(sys.stdin)
        if args.kind == "sheet":
            render_sheet(args.job_dir, req)
            result: dict[str, Any] = {"ok": True}
        else:
            result = {"ok": True, "plan": section_plan(args.job_dir, req)}
    except Exception:  # noqa: BLE001 - one failure code; the API logs the redacted stderr tail
        if not args.job_dir.is_dir():
            # Whatever failed (a missing source.pdf, the mkdir above), the job was deleted under us: an
            # expected race, not an error worth a traceback.
            print(json.dumps({"ok": False, "code": GONE}), flush=True)
            return 2
        traceback.print_exc()
        print(json.dumps({"ok": False, "code": "internal"}), flush=True)
        return 1
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
