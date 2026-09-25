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

## Previous attempt (RETRY — read this first)

Attempt 1 built everything (engine `ef828ab` on `feature/web-mode`: `detect.py`, `headed_book`
fixture, 30 tests, README; docs `434d9f5`) and failed the review gate with **3 confirmed
findings** — full text in `docs/findings/STORY-002-review.md`. Keep attempt 1's work; fix
forward on the same branch with ONE commit:
`fix: STORY-002 - gate r1: straddling wraps merge, roman page numbers only at the page edge, indirect outline destinations carry y`.

1. **Wrap merge across column classes** (`detect.py:184-196`). A wrapped heading whose lines
   straddle the full-width threshold (0.70·W line + 0.21·W line, same x0) must merge. Match
   `locate_heading`'s rule (`index.py:229`, grouping by `is_left(x0)`): bucket lines by x0 side,
   not by the 3-way `_col`; the merged candidate's `col` is "full" if any of its lines is full,
   else the side. Keep size tolerance + `wrap_gap` + 3-line cap. Tests: the synthetic straddle
   case (full→left, and left→full), plus a genuinely **1-column synthetic book** with a wrapped
   chapter title (the Architecture's detector corpus asks for 1-column coverage). Real-book
   check (read-only, optional): Maciocia p480 ch. 30 "Identification of Patterns / according to
   the Eight Principles" with `wrap_gap=30` should be ONE candidate.
2. **Page-number filter** (`detect.py:25`, applied `:186`). Real headings made only of
   i/v/x/l/c/d/m ("C", "D", "Mild", "Dill", "Civil", "Mix", "Vivid", "Ill") must survive.
   Rule: digit-only (optionally "page N") lines are page numbers anywhere; a roman token counts
   only if it is a **well-formed roman numeral** AND sits in the page-edge strip
   (header/footer strip `_running` already computes). Tests: the A–Z glossary (one 20 pt letter
   per page → 26 candidates), the "Dill/Mild/…" words, and the existing page-number forms
   ("xiv" in the footer is still excluded).
3. **Indirect outline destinations** (`detect.py:46-60`, `_dest_top`). `xref_get_key` returns
   kind `"xref"` for `/D n 0 R` / `/Dest n 0 R`; resolve the referenced object (it holds the
   destination array, or a dict with `/D`) and continue through the existing array path. Test:
   an outline item `/A <</S/GoTo/D n 0 R>>` → y equals the direct-array control.

Also: update the STORY-002 findings file (add a "Gate r1 fixes" section; keep Status done),
and re-check that `KICKOFF-STORY-003.md` is still accurate (edit only if your fix changes an
API it cites). Full suite must stay green (102 + your new tests).

## Attempt 3 (Shuma approved ONE more targeted fix, 2026-09-25) — READ THIS FIRST

Gate r2 (on 4d8d375) confirmed 2 findings with ONE root cause — see `docs/findings/STORY-002-review.md`
§ Round 2. `_at_edge` floors the page-edge strip at `EDGE_SHARE·H` (≈95 pt) regardless of the
caller's bands; page-opening headings (top ≈ 60–70 pt) sit in it, so roman-letter headings
(C D I L M V X) are dropped as folios and digit-masked numbered headings ("Lesson 1..10" on ≥30%
of pages) collapse into one "running header".

**Binding rules (implement exactly; fix forward on feature/web-mode, one commit
`fix: STORY-002 - gate r2: the caller's bands are the only page edge; numbered headings are not running headers`):**

1. **No hidden edge zone.** Delete `EDGE_SHARE` and the floor. "At the edge" = inside the
   caller's `header_band` (top) or `footer_band` (bottom), nothing else.
2. **Folios:** digit-only lines ("12", "- 12 -", "Page 12") are page numbers anywhere (unchanged).
   A well-formed roman numeral is a page number ONLY at the edge per rule 1.
3. **Running headers, two kinds:**
   a. *Verbatim*: identical normalized text (case/whitespace-folded, digits NOT masked) at the same
      size on ≥ max(2, ceil(0.3·pages)) pages → excluded anywhere (a line repeated verbatim on 30% of
      pages is furniture, not a section start).
   b. *Digit-varying* (the "Chapter 3 · Title  45" kind): digit-masked key on ≥ 30% of pages →
      excluded ONLY for lines at the edge per rule 1.
   Consequence: with `header_band=footer_band=0`, digit-varying running headers are no longer
   auto-excluded — that is accepted (the user's bands define the edge). Update the AC-6 tests to
   this rule (verbatim running lines still excluded with bands 0; digit-varying ones excluded with
   default bands) and record the interpretation in findings.
4. **Tests that must exist and pass:** the A–Z glossary with letters at baseline 90 (top ≈ 68.6 —
   the r2 geometry, NOT 120) → 26 candidates; the 30-page "Lesson N" workbook (10×3 pages,
   baseline 90) → 10 candidates; the "Rare Header 1/2/3" test rewritten to the new rule; folio
   forms in the default bands still excluded; a verbatim running header still excluded.
   Repros: scratchpad probe_glossary*.py, probe_numbered*.py (orchestrator scratchpad dir).
5. Real-book check (read-only): Maciocia default candidates count before/after — report both;
   chapter 30/31 must still be one candidate each at wrap_gap=30.

**Scope boundary for this attempt (the reviewer is told the same):** only the r2 root cause and
anything your change breaks. Other heuristic edge cases found later are logged as follow-ups,
not blockers — the section list is user-editable by design (PRD A-1).
Update findings (add "Gate r2 fixes"), keep KICKOFF-STORY-003 accurate (its "Orchestrator
decisions" section is binding for 003 — don't remove it), push both repos to origin + gitea.
