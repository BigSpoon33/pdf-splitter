# KICKOFF — STORY-002: Engine: outline and big-heading candidate detection

## What you're walking into

Two repos, one product ("pdf-splitter": a public site where people drop a big PDF and get one
PDF per chapter/section back).

- **Engine**: `~/Documents/Repos/monograph-splitter` (GitHub `BigSpoon33/pdf-splitter-engine`,
  Python package `monograph_splitter`, v0.3.1, PyMuPDF 1.28.2). **You write code here**, on branch
  `feature/web-mode`. `main` is 6fd22fc, and the tip is **742bfbe** (STORY-001), pushed to `origin` and `gitea`. The story
  file says "branch `story/STORY-002`", but the loop's convention is one engine branch
  `feature/web-mode` for all engine stories. Stay on it.
  Tests: `uv run --group dev pytest` → baseline **72 passed**.
- **Web/planning**: `~/Documents/Repos/pdf-splitter` on branch `feature/mvp`. It holds the PRD,
  Architecture, stories and findings. Read `docs/Architecture.md` § "Engine additions
  (monograph-splitter 0.4.0)", then `docs/stories/STORY-002.md` (its ACs are authoritative), then
  `docs/findings/STORY-001-findings.md`. `docs/loop-state.json` belongs to the orchestrator:
  never stage it.

Engine layout: `profile.py` (`Profile`, TOML `SCHEMA`, `_check_type`, `load_profile`, and now the
web-mode additions), `entries.py`, `session.py` (`Book`), `index.py` (`page_lines`,
`locate_heading`, `heading_anchor`, `add_heading_anchors`, `index_book`), `cuts.py`, `classify.py`
(`is_left`, `same_column`), `render.py`, `verify.py`, `cli.py`, `diff_manifest.py`,
`review/server.py`. Tests build synthetic books with PyMuPDF (`tests/fixtures.py`: `FakeBook`,
`scenario_book` = labels mode, `heading_book` = headings mode, both two-column). No test reads a
real book. Keep it that way.

## What STORY-001 established (use these, don't re-invent)

- `profile.profile_from_dict(d, base=WEB_BASE) -> Profile` (`profile.py:242`). Only `WEB_KEYS`
  (`profile.py:233`: `column_split, header_band, footer_band, redact_top, heading_min_size,
  heading_match, heading_wrap_gap, max_span, single_column`). Anything else → `ProfileError`.
  `sha256` = hash of the effective values (the index cache key).
- `profile.WEB_BASE` (`profile.py:223`): headings mode, `script_regex=""`, `break_patterns=()`,
  `max_span=200`, `subheader_bottom=redact_top`, and **`sheet_offset=0`**.
- `profile.SINGLE_COLUMN` (`profile.py:238`) = `{column_split: 0.999, full_width_ratio: 0.0}`.
- `entries.entries_from_rows(rows, where="entries") -> EntryList` (`entries.py:53`); rows are
  `{name, page, heading?, stop?, source?}`.
- `session.Book.open(entries=Path | str | list[dict] | EntryList, profile=Profile | path | name)`.

**Contract = the tests**, not prose: `tests/test_profile_dict.py` (settings dict, single-column
geometry, hash) and `tests/test_entries_rows.py` (row validation, `Book.open` from rows, and
"page 1 → sheet index 0" at `test_book_profile_accepts_a_profile_instance_from_settings`).
Your `outline_entries` / `heading_candidates` rows must be valid input for
`Book.open(entries=<your rows>, profile=profile_from_dict(...))` unchanged. Prove it with a
test like `test_book_opens_from_rows_an_entry_list_or_a_path_with_the_same_plans`.

## Critical gotchas

1. **Pages are 1-based sheet numbers, engine `sheet_offset=0`.** The docs (PRD A-5,
   Architecture, ADR) say "`sheet_offset = 1`". That is the intent (page 1 = first sheet) written
   with the wrong engine value, because the engine maps page N to sheet index `N + sheet_offset - 1`.
   `detect.*` must emit `page = sheet_index + 1`. Don't "fix" WEB_BASE back to 1.
2. **Outline destinations** (PyMuPDF 1.28.2, verified): `doc.get_toc(simple=False)` →
   `[lvl, title, page(1-based), dest]`. `dest["to"]` is a `Point` in **top-left** coordinates
   (the value written by `set_toc` round-trips unchanged). An item written without a dest dict
   still reads back `to = Point(72, 36)`, a default and not a real target. Decide how "y present
   when the destination carries a point" treats that (e.g. only when `dest["kind"] == LINK_GOTO`
   and the source carried one), and test both cases. `page` can be `-1` for broken links. Skip
   those rows or report them.
3. **`locate_heading` is unchanged by contract** (story note). It only considers lines with
   `top ≥ title_min_y` (30.0 in WEB_BASE) and `size ≥ heading_min_size` (12.5 default), with
   fuzzy match `heading_match` (0.85). A candidate's `heading` must be the text as it appears on
   the page (joined wrapped lines), or the later Book will flag `heading-not-found`. Outline titles:
   strip leading numbering only.
4. **`WEB_BASE` still has `chapter_only`** (`^\s*chapter\s*\d+…`, ≥ 14 pt, ≤ 28 chars). Such a
   sheet is a chapter break that ends the previous entry. It doesn't affect detection. Note it if your
   synthetic book trips it when you round-trip through `Book`.
5. **`page_lines(page)`** (`index.py:22`) returns `(y0, y1, x0, x1, text, size)` sorted by y, one
   `get_text("dict")` per call (AC-7: call it once per page). `size` is the max span size on
   the line. `is_left(x0, w, prof)` / `same_column` in `classify.py` need a Profile. Either build
   one via `profile_from_dict({"column_split": …})` or compare `x0 < column_split * w` directly
   (Architecture signature: `heading_candidates(doc, *, min_ratio=1.3, max_len=90, header_band,
   footer_band)`, plus `wrap_gap` per AC-3). "full" = line wider than `full_width_ratio · W`
   (0.55 default), the same rule `heading_anchor` uses.
6. `index_book` caches to disk only when given a path. Detection must write nothing (AC-7).

## Recommended ordering

1. `tests/fixtures.py`: add a generic headed-book builder (`FakeBook` already has `page()`,
   `text()`, `body()`). Body 9.5 pt, chapter 16 pt, section 12 pt, 3 chapters (one starting
   mid-right-column), a running header repeated on every page in the header band, page-number
   lines in the footer, plus an outline via `doc.set_toc(...)` with and without dests.
2. `detect.outline_levels(doc)` → counts per level; no outline → `[]` (AC-2).
3. `detect.outline_entries(doc, level)` → `[{name, page, heading, level, y?}]` (AC-1).
4. `detect.heading_candidates(...)`: char-weighted modal body size; the threshold; bands;
   `max_len`; wrap merge (reuse the `heading_wrap_gap` idea from `locate_heading`); running-header
   and page-number exclusion (AC-6); size clustering into levels within 0.5 pt (AC-4); `col`.
5. `tests/test_detect.py`: AC-1..7 incl. the AC-5 scenario and `min_ratio > 16/9.5 → []`, plus a
   round-trip through `Book.open(entries=candidates_as_rows, profile=profile_from_dict({...}))`
   showing every level-1 heading located (no `heading-not-found`).
6. Full suite green (≥ 72 + yours); the labels/headings synthetic manifests must not change
   (you don't touch `cuts`/`index`, so `monograph-splitter-diff` stays 0).

## Conventions

- Python 3.11+, match the surrounding style: module docstring with `# Docs:` header line,
  `from __future__ import annotations`, terse WHY comments, no WHAT comments.
- Commit on `feature/web-mode` in the engine repo:
  `feat: STORY-002 - detect: outline entries and big-heading candidates for arbitrary books`,
  ending with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Stage explicit paths only. Never `git add -A` / `git add .`; never `reset --hard`.
- No version bump or tag (STORY-003). Push: `git push origin feature/web-mode` and
  `git push gitea feature/web-mode`. Never push to `main`.
- Findings + KICKOFF-STORY-003 are committed in `~/Documents/Repos/pdf-splitter` on `feature/mvp`
  as `docs: STORY-002 - findings + KICKOFF-STORY-003`, pushed to `origin` and `gitea`.

## Authority

- Free: the engine's `src/` and `tests/`, README additions describing the new API, the
  pdf-splitter repo's `docs/findings/`, `docs/KICKOFF-*` and the STORY-002 status lines.
- Do not touch: `~/Documents/AI/Inkwell`, the bundled profiles' values, the CLI's behaviour,
  `locate_heading`, `docs/loop-state.json`, PRD/Architecture (report doc errors in findings).

## Stopping conditions (BLOCKED protocol)

- An AC can't be met without changing cut/classify/locate behaviour for existing profiles.
- An existing test fails and the cause isn't your change.
- You'd need credentials, cloud resources, money, or to touch the Inkwell repo.

## Final report shape

Per-AC ✅/❌ with file:line, test counts (before 72 / after N), commits (both repos), anything
STORY-003 (`Book.cut_all` + CLI on it, 0.4.0 release) should know.

## Orchestrator addendum (after STORY-001 review)

- STORY-001 passed review clean. The docs now say `sheet_offset=0` (PRD A-5, Architecture, ADR-003).
- One-line cleanup to fold into your commit: the comment at `src/monograph_splitter/profile.py:218-219` still claims the pdf-splitter docs write "sheet_offset = 1"; reword it (the docs are fixed).
- Real-book check available for outline detection: Maciocia *Foundations* (1319 sheets, has a PDF bookmark outline — Inkwell's STORY-219 built its entries from it) is at
  `~/Documents/Vaults/TCM_Knowledge_Base/Books/Maciocia, Giovanni - The foundation of Chinese medicine_ a comprehensive text (2015, Elsevier) - libgen.lc.pdf`.
  READ-ONLY; write any outputs to a temp dir. Optional sanity check, not a test (tests never read real books). Report level counts + a few sample rows in findings.
