"""The analyze job's engine calls: index the book, collect both candidate section lists, suggest one.

Pure apart from the index cache it writes under `work_dir`; the task (`worker/task.py`) owns the job row
and the output files. Shapes: Architecture § Data Types (Analysis, Plan). Pages are 1-based sheets (ADR-003).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pymupdf
from monograph_splitter.detect import heading_candidates, outline_entries, outline_levels
from monograph_splitter.index import index_book
from monograph_splitter.profile import WEB_BASE, profile_from_dict

# PyMuPDF 1.28 raises the same MuPDF error two ways: its `_extra` helpers (text extraction, `get_text`)
# re-raise as a plain RuntimeError, while its raw bindings (`fz_load_page`, xref lookups, page labels)
# raise `pymupdf.mupdf.FzErrorSystem`/`FzErrorFormat`/… — Exception subclasses under `FzErrorBase`, NOT
# RuntimeErrors. Anything that must treat "a MuPDF failure" uniformly catches this tuple.
try:
    from pymupdf.mupdf import FzErrorBase

    MUPDF_ERRORS: tuple[type[Exception], ...] = (RuntimeError, FzErrorBase)
except ImportError:  # pragma: no cover - a PyMuPDF build without the raw bindings only raises RuntimeError
    MUPDF_ERRORS = (RuntimeError,)

# (done, total, message): pages indexed so far, the page count, and what the job is doing.
Progress = Callable[[int, int, str], None]

INDEX_CACHE = ".book-index.json"   # the name Book.open uses in its out dir, so the cut job finds it
MAX_OUTLINE_LEVEL = 3
# The Plan's limits (Architecture § Data Types); the default plan keeps to them so a client can PUT it back.
MAX_SECTION_NAME = 120
MAX_HEADING = 500
SUGGEST_RANGE = (5, 60)            # a heading level with this many items reads as a table of contents
MSG_INDEXING = "Indexing pages"
MSG_OUTLINE = "Reading the outline"
MSG_HEADINGS = "Finding headings"

# The Plan's settings keys (Architecture § Data Types) at the web engine defaults. The analyze index is
# built under exactly this profile, so a cut or preview of the unchanged default plan hits its cache.
DEFAULT_SETTINGS: dict[str, Any] = {
    "column_split": WEB_BASE.column_split,
    "single_column": False,
    "header_band": WEB_BASE.header_band,
    "footer_band": WEB_BASE.footer_band,
    "heading_min_size": WEB_BASE.heading_min_size,
}


class _Counted:
    """The document, reporting each page as the engine's `index_book` finishes it. v0.4.1 has no progress
    hook and indexes with `[index_page(page, prof) for page in book]`; wrapping the iterator keeps the
    engine's own cache key and file format instead of copying them here."""

    def __init__(self, doc: pymupdf.Document, progress: Progress | None) -> None:
        self._doc = doc
        self._progress = progress

    def __getattr__(self, name: str) -> Any:
        return getattr(self._doc, name)

    def __getitem__(self, i: int) -> Any:
        return self._doc[i]

    def __len__(self) -> int:
        return self._doc.page_count

    def __iter__(self) -> Iterator[Any]:
        total = self._doc.page_count
        for i in range(total):
            yield self._doc[i]
            # Resumed only once the engine has indexed page i, so this counts finished pages.
            if self._progress:
                self._progress(i + 1, total, MSG_INDEXING)


# PyMuPDF decodes a malformed text string (a bookmark title, a heading's ToUnicode output, a label) with
# `surrogateescape`, so bad bytes come back as lone U+DC80..U+DCFF, and a stray UTF-16 surrogate stays one.
# Python's str holds them, but strict UTF-8 (`analysis.json`, the API's responses) can't.
_SURROGATES = re.compile("[\ud800-\udfff]")


def clean_text(s: str) -> str:
    """`s` with every lone surrogate replaced by U+FFFD, one per code unit, so the rest of the title survives."""
    return _SURROGATES.sub("�", s) if _SURROGATES.search(s) else s


def json_safe(obj: Any) -> Any:
    """`obj` with `clean_text` applied to every string in it (values and keys), so anything built from it
    (the default plan's section names, the API's payloads) is UTF-8-safe without each caller remembering."""
    if isinstance(obj, str):
        return clean_text(obj)
    if isinstance(obj, dict):
        return {json_safe(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [json_safe(v) for v in obj]
    return obj


def page_labels(doc: pymupdf.Document) -> list[str]:
    """Printed page labels, "" where there are none. Only a UI hint (ADR-003), so a label tree PyMuPDF
    can't parse (a real book here has one: `rule_dict` raises on an empty /St) or that MuPDF itself
    refuses (a raw-binding `FzError*`) costs the labels, never the analysis."""
    try:
        if not doc.get_page_labels():
            return [""] * doc.page_count
        return [doc[i].get_label() for i in range(doc.page_count)]
    except (ValueError, IndexError, KeyError, *MUPDF_ERRORS):
        return [""] * doc.page_count


def analyze(pdf_path: Path, work_dir: Path, progress: Progress | None = None) -> dict[str, Any]:
    """The Analysis for one PDF (Architecture § Data Types), with `suggested` filled in."""
    prof = profile_from_dict(DEFAULT_SETTINGS)
    work_dir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf_path, filetype="pdf") as doc:
        total = doc.page_count
        if progress:
            progress(0, total, MSG_INDEXING)
        # log: the engine prints to stdout by default, and the task's result must stay the last stdout line.
        index = index_book(_Counted(doc, progress), work_dir / INDEX_CACHE, prof, log=lambda *_: None)
        if progress:
            progress(total, total, MSG_OUTLINE)
        levels = [row for row in outline_levels(doc) if row["level"] <= MAX_OUTLINE_LEVEL]
        counts = {row["level"]: row["count"] for row in levels}
        depth = max(counts, default=0)
        items = [item for lvl in sorted(counts) for item in outline_entries(doc, lvl)]
        if progress:
            progress(total, total, MSG_HEADINGS)
        headings = heading_candidates(doc, profile=prof)
        # Every string the engine or PyMuPDF hands over enters the dict here, and only here.
        analysis: dict[str, Any] = json_safe({
            "pages": total,
            "pageLabels": page_labels(doc),
            "size": [{"W": pg["W"], "H": pg["H"]} for pg in index],
            "outline": {"levels": [counts.get(lvl, 0) for lvl in range(1, depth + 1)], "items": items},
            "headings": headings,
        })
    analysis["suggested"] = suggest(analysis)
    return analysis


def _distance(count: int) -> int:
    lo, hi = SUGGEST_RANGE
    return lo - count if count < lo else max(0, count - hi)


def suggest(analysis: dict[str, Any]) -> dict[str, Any]:
    """Outline level 1 when it has ≥ 2 items; else the heading level whose count is closest to 5–60 (the
    bigger type wins a tie); else nothing to suggest (`manual`: the user pastes a list)."""
    levels = analysis["outline"]["levels"]
    if levels and levels[0] >= 2:
        return {"source": "outline", "level": 1}
    heading_levels = analysis["headings"]["levels"]
    if heading_levels:
        best = min(range(len(heading_levels)), key=lambda i: (_distance(heading_levels[i]["count"]), i))
        return {"source": "headings", "level": best + 1}
    return {"source": "manual", "level": None}


def default_plan(analysis: dict[str, Any]) -> dict[str, Any]:
    """The Plan a job starts from: the suggested list, default settings, no overrides."""
    source, level = analysis["suggested"]["source"], analysis["suggested"]["level"]
    if source == "outline":
        rows = [it for it in analysis["outline"]["items"] if it["level"] == level]
    elif source == "headings":
        rows = [c for c in analysis["headings"]["candidates"] if c["level"] == level]
    else:
        rows = []
    return {
        "source": source,
        "settings": dict(DEFAULT_SETTINGS),
        "sections": [
            {"name": r["name"][:MAX_SECTION_NAME], "page": r["page"], "heading": r["heading"][:MAX_HEADING]}
            for r in rows
        ],
        "overrides": {},
    }


def ranges_plan() -> dict[str, Any]:
    """The Plan a page-range job starts from (ADR-009): no spans yet, so nothing can be cut until the visitor types
    some. The settings ride along unused, so every saved plan has the same four keys."""
    return {"source": "ranges", "settings": dict(DEFAULT_SETTINGS), "sections": [], "overrides": {}}
