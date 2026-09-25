"""Preflight an uploaded PDF in a throwaway subprocess:
`python -m pdf_splitter.preflight [--max-pages N] [--cpu-limit S] -- PATH`.

Prints one JSON object on stdout: `{"ok": true, "pages": N}` or `{"ok": false, "code": "<code>"}`.
It runs out of process (ADR-005) because MuPDF parses hostile input; the API enforces the timeout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pymupdf

from .worker import sandbox

MAGIC = b"%PDF-"
PROBE_PAGES = 12
MIN_TEXT_CHARS = 50


def probe_indices(page_count: int, k: int = PROBE_PAGES) -> list[int]:
    """Up to `k` evenly spaced 0-based page indices, first and last included."""
    if page_count <= k:
        return list(range(page_count))
    return sorted({round(i * (page_count - 1) / (k - 1)) for i in range(k)})


def check(path: Path, max_pages: int) -> dict[str, Any]:
    with path.open("rb") as f:
        if f.read(len(MAGIC)) != MAGIC:
            return {"ok": False, "code": "not_pdf"}
    try:
        # filetype pins the PDF parser, so MuPDF never content-sniffs its way into another format.
        doc = pymupdf.open(path, filetype="pdf")
    except Exception:  # noqa: BLE001 - any parse failure is the same user-facing answer
        return {"ok": False, "code": "unreadable"}
    with doc:
        if doc.needs_pass:
            return {"ok": False, "code": "encrypted"}
        pages = doc.page_count
        if pages > max_pages:
            return {"ok": False, "code": "too_many_pages", "pages": pages}
        chars = 0
        for i in probe_indices(pages):
            chars += len("".join(doc[i].get_text().split()))
            if chars >= MIN_TEXT_CHARS:
                break
        if chars < MIN_TEXT_CHARS:
            return {"ok": False, "code": "no_text_layer", "pages": pages}
        return {"ok": True, "pages": pages}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pdf_splitter.preflight")
    parser.add_argument("--max-pages", type=int, default=2000)
    # Opt-in so tests can call main() in-process without limiting the test runner itself.
    parser.add_argument("--cpu-limit", type=int, help="apply the worker's rlimits, with this RLIMIT_CPU")
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    if args.cpu_limit is not None:
        # Before the file is opened: a tiny nested-XObject PDF can balloon MuPDF past 700 MB inside the
        # API's 10 s window (STORY-005 review). Self-applied rather than `preexec_fn`, because the API
        # spawns this from threadpool threads.
        sandbox.apply({**sandbox.limits(0), "cpu": args.cpu_limit})
    # MuPDF's warnings on hostile files can quote file content; the API never needs them.
    pymupdf.TOOLS.mupdf_display_errors(False)
    # Last line of stdout by contract: PyMuPDF itself may print notices to stdout before it.
    print(json.dumps(check(args.path, args.max_pages)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
