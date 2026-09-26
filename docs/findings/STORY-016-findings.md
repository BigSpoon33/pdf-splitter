# Findings — STORY-016
**Date:** 2026-09-26
**Status:** done

Engine commit **`116a4bb`** on `feature/web-mode` (`BigSpoon33/pdf-splitter-engine` = `origin`, Gitea mirror =
`gitea`), `fix: STORY-016 - cut rectangles use each sheet's own size (0.4.2)`, pushed to both remotes with the
annotated tag **`v0.4.2`** on it (`git ls-remote --tags` on both: `v0.4.2^{}` = `116a4bb7`, `v0.4.1^{}` still
`8e52cc34`, `v0.4.0^{}` still `c8935d25`; `main` untouched at `6fd22fc`). This repo pins `@v0.4.2` in **`c410caa`**
(`chore: STORY-016 - pin monograph-splitter v0.4.2`): `uv.lock` resolves
`git+https://github.com/BigSpoon33/pdf-splitter-engine?rev=v0.4.2#116a4bb7b0a08001a27f8efcaeb9289ed62bd0d7`, and a live
`GET /api/health` answered `"engine_version": "0.4.2"`.

## AC Verification
- [x] AC-1: every rectangle uses the size of the sheet it lands on — engine
  `src/monograph_splitter/cuts.py:182` `cut_rects(p, w, h, prof, last_size=None)`: `w, h` size the excerpt's FIRST
  sheet (the start cut's rectangles), and when `last_size` is given the end-cut block (`:217-220`) rebinds `w, h`,
  `bottom = h − footer_band` and `split = column_split × w` to the LAST sheet before any end rectangle is built (the
  full-width span `(0, y, w, bottom)`, the column cuts at `split`, the "later bands" span). Callers:
  `session.py:166` `Book.rects` passes `last_size=self.page_size(p["sheet1"])` (both sizes from the index's per-page
  `W`/`H`, no page opened); `render.py:17` `write_excerpt` passes `(out[-1].rect.width, out[-1].rect.height)` (the
  excerpt's pages ARE the book's pages, so `out[0]`/`out[-1]` are `sheet0`/`sheet1`); `render.py:33` `render_review`
  passes `doc[last].rect` and now draws the ruler and the cut label in each PNG's own `w, h` (`:48`). The old call
  shape `cut_rects(p, w, h, prof)` is unchanged, and `last_size=(w, h)` gives the same list — the uniform-book
  contract (`tests/test_mixed_sizes.py::test_the_end_cut_is_placed_in_the_last_sheets_own_geometry` asserts both).
  The review editor's `review/server.py:127,137` `page_size` calls feed the editor's single `pageSize` (its `app.html`
  draws every sheet in the first sheet's viewBox) — they compute no rectangles, so they are untouched; the rects the
  editor receives from `_plan_view` → `Book.rects` are now per-sheet (see Out-of-Scope).
- [x] AC-2: `tests/fixtures.py:199` `mixed_size_book` — a headings-mode book whose p1 is 522.72 × 789.6 (Alpha
  Pattern top-left, body in both columns) and p2 is **700 × 600** (left: 12 "alpha tail" lines long enough to cross
  the NARROW sheet's gutter ≈ 255 pt but not the wide one's ≈ 341 pt; right, at x = 380: Beta Pattern at the top of
  the column, then body running past the narrow sheet's right edge 522.72). Alpha's plan spans both sheets with an
  end cut in the right column of the wide sheet. `tests/test_mixed_sizes.py` (6 tests):
  `::test_the_fixture_really_straddles_the_two_gutters` proves the fixture exercises the bug (`narrow_split < x1 <
  wide_split` for every tail line; Beta's lines start past `wide_split` and end past `W`);
  `::test_the_written_excerpt_keeps_alphas_tail_whole_and_drops_betas_column` writes the excerpt and asserts on the
  last page's text positions: exactly the 12 whole tail lines between the bands, every `x1 < 0.487 × 700`, no "Beta
  Pattern" / "beta body" / Beta's "Clinical manifestations" anywhere, the running header and folio kept, and
  `verify_headings(...) == []` (0 leaks); it also asserts `Book.rects(p)` equals `cut_rects` computed from the written
  excerpt's own page rects. `::test_cut_all_reports_no_leak_on_the_mixed_book` runs `cut_all(verify=True,
  preview=True)` → `leaks == {}`, `missing == []`; `::test_review_pngs_are_drawn_in_each_sheets_size` → the last PNG
  is 700 × 600 at 72 dpi. Against `v0.4.1` the same tests fail (`Book.rects` → `[254.6, 63.6, 522.7, 757.6]` instead
  of `[340.9, 63.6, 700.0, 568.0]`, and the old engine's output clips Alpha's own tail at the narrow gutter —
  "…that reaches acro" — while leaving Beta's overflow "t the narrow edge" at x = 527–602; verified by running the
  fixture through a `git worktree` of `v0.4.1`).
- [x] AC-3: diff gate — see § Diff gate below: **0 changed** on all six synthetic runs (manifests byte-identical), on
  Maciocia (0 of 211) and on Chen & Chen (0 of 231); the `.book-index.json` files are identical pre/post on both real
  books; every excerpt's extracted text is identical (211/211, 231/231). `ENGINE_VERSION` is still 17
  (`src/monograph_splitter/__init__.py:17`; `tests/test_web_mode.py:191` pins it); `index.py`/`detect.py` untouched.
- [x] AC-4: release + pin + manual check. Engine: `pyproject.toml:3`, `uv.lock`, `__init__.py:16` say `0.4.2`,
  `tests/test_web_mode.py:190` pins the literal; tag `v0.4.2` on both remotes (above). Web: `pyproject.toml:12`
  `@v0.4.2`, `uv lock` + `uv sync` done, `tests/test_health.py:27` → `"0.4.2"`, Architecture `:28`, `:270` (ADR-001),
  `:351` (Dependency Map) say `v0.4.2`, and a "0.4.2 (STORY-016, as built): per-sheet geometry" bullet under
  "Engine additions" (`docs/Architecture.md:93-99`). Manual check on `mixed.pdf` (3 pages: 522.72 × 789.6, then two
  700 × 600; "Alpha Chapter" top-left of p1, "Beta Chapter" at the top of p2's RIGHT column at x = 380, Alpha's tail
  in 30 long left-column lines on p2) uploaded through the SPA with headless Chromium (API `:8010`, Vite `:5181`):
  analysis `size = [{522.7, 789.6}, {700, 600}, {700, 600}]`, 2 headings sections; section 1's preview drew
  sheet 2 in `viewBox 0 0 700 600` with ONE hatch `<rect data-testid=removed>` = `x 340.9, y 69.9, w 359.1, h 498.1`
  (→ `[340.9, 69.9, 700.0, 568.0]`), the same as `POST /sections/0/plan` → `rects: [[2, [340.9, 69.9, 700.0,
  568.0]]]`; "Split into 2 PDFs" → `done`; `GET /sections/0.pdf` (23,839 B) has pages 522.7 × 789.6 and 700 × 600,
  and its page 2 rendered at 72 dpi has **0 ink pixels inside that rect** and 25,900 in the left column beside it,
  all 30 "alpha tail" lines kept whole (`x1 = 340 < 340.9`), no "beta" text anywhere. Screenshot (315 × 292, 56 KB):
  `docs/findings/STORY-016-mixed-preview.png` — the last sheet drawn in its own aspect, the gutter dashed at ≈ 341,
  the hatch over Beta's column.

## Test Results
**Command:** `cd ~/Documents/Repos/monograph-splitter && uv run --group dev pytest -q`
**Result:** pass — `156 passed, 2 warnings` (150 before + 6 in `tests/test_mixed_sizes.py`).

**Command:** `cd ~/Documents/Repos/pdf-splitter && uv run pytest -q && uv run ruff check`
**Result:** pass — `427 passed in 94.00s`; `All checks passed!` (427 = the kickoff's 426 + the orchestrator's gate r3
test `cbb1a76`; nothing added here — the engine test is the contract).

**Command:** `cd web && bun run check && bun run test && bun run build`
**Result:** pass — `337 FILES 0 ERRORS 0 WARNINGS`; `Test Files 21 passed (21)`, `Tests 284 passed (284)`;
`dist/assets/index-CTEkmUWg.js 109.15 kB │ gzip: 38.89 kB` (unchanged — no SPA code touched).

### Diff gate (AC-3), exact runs
Pre = `git worktree add <scratch>/engine-v0.4.1 v0.4.1` (+ the new `tests/fixtures.py` copied in so the same
fixture code built the books); post = the working tree at what became `116a4bb`. All outputs in the scratchpad
(deleted afterwards); the real books read from `~/Documents/Vaults/TCM_Knowledge_Base/Books/` and the entries inputs
from `~/Documents/AI/Inkwell/docs/planning/curriculum/tools/` — both read-only, nothing written there.

- **Synthetic** (`scenario_book` + `tests/profile-test.toml`; `heading_book` + `tests/profile-headings.toml`), each
  through `uv run monograph-splitter --pdf … --profile … --entries … --out …` three ways: `--verify`,
  `--verify --no-redact`, `--verify --limit 2 --only "<3 names>"`. `monograph-splitter-diff pre/manifest.json
  post/manifest.json` → `0 of 7`, `0 of 7`, `0 of 2` (scenario) and `0 of 6`, `0 of 6`, `0 of 2` (headings) entries
  changed; the six manifest pairs are **byte-identical** (`cmp`), the CLI logs identical once elapsed seconds and the
  out path are masked.
- **Maciocia *Foundations*** (1319 sheets, all 535.7 × 697.3): entries JSON (273 rows) built in scratch with the logic
  of Inkwell's `extract_pattern_pdfs.build_entries()` from `foundations_pattern_entries.json`; `--profile
  maciocia-foundations --verify`. `0 of 211 entries changed`; `.book-index.json` identical; logs identical (211 PDFs,
  190 top / 193 bottom cuts, notes `{'uncut-banner-above': 19}`, no flags); `bytes` differs on 3 rows by 1 byte
  (MuPDF output nondeterminism, as in STORY-003); extracted text identical on 211/211 excerpts.
- **Chen & Chen *Formulas*** (1658 sheets: 1486 × 522.7 × 789.6, 168 × 505.9 × 787.9, 3 × 522.7 × 786.2, 1 ×
  585.8 × 780.5 — NOT a uniform book): `--profile chen-chen-formulas --from-vault
  ~/Documents/Vaults/TCM_Knowledge_Base --vault-folder TCM_Formulas --known-pages cc_formula_pages.json
  ccf_anchors.json --verify` (the same inputs `extract_formula_pdfs.py` uses; 235 vault notes → 231 PDFs + 2
  skipped). `0 of 231 entries changed`; index identical; logs identical (92 top / 110 bottom cuts, flags
  `{'start-name-fuzzy': 6, 'end-at-promoted-header': 1, 'passed-uncertain-header': 7, 'start-page-corrected': 1,
  'end-cut-estimated': 1, 'long-span': 1}`); `bytes` differs on 4 rows; extracted text identical on 231/231. Exactly
  ONE excerpt has its end cut on a sheet sized differently from its first (Chai Xian Tang: 522.7 × 786.2 → 522.7 ×
  789.6, a full-width end cut): its footer edge moved 3.4 pt down, over no glyph — same text, 23 bytes smaller. So the
  fix reaches a real Inkwell book, harmlessly.

## Bugs Found
- Pre-existing flake, not touched (existing assertions are out of authority):
  `tests/test_headings.py::test_cli_end_to_end_in_headings_mode` compares two manifest rows including `bytes`
  (`man2["Beta Pattern"] == man["Beta Pattern"]`, `:181`); MuPDF's output size varies by a few bytes between saves,
  so the test fails ≈ 1 run in 5–7 (`{'bytes': 1540} != {'bytes': 1537}`). Seen once in a full-suite run before any
  version bump, then 6 green runs; it reproduces on `v0.4.1` too — 2 failures in 12 runs of that one test in the
  `v0.4.1` worktree (the STORY-003 findings describe the same nondeterminism on real books). Fix candidate for the engine's next chore: drop `bytes` from that equality.
- None in this repo.

## Handoff Context for Next Session
STORY-013 is deploy-only in THIS repo (`feature/mvp`, tip `c410caa` + the docs commit after it); the engine is
pinned at `v0.4.2` and nothing in the compose stack needs anything from STORY-016 except that `uv sync --frozen`
inside the image must fetch `git+https://github.com/BigSpoon33/pdf-splitter-engine@v0.4.2` (public GitHub, no LAN) —
AC-5's whole point. Port 8000 is taken on this laptop (API on `:8010` for live checks); the compose stack's own
ports must not collide with the dev servers, and a `read_only` worker still needs `PDFSPLIT_JOBS_DIR` and `/tmp`
writable (the sandbox launcher runs `sys.executable -m pdf_splitter.worker.sandbox` — the image's venv python).

## Out-of-Scope Items
- The engine's own review editor (`review/app.html`) draws every sheet in `d.pageSize` (the first sheet's size); the
  per-sheet rects it now receives from `Book.rects` are correct in PDF points but land in the wrong viewBox on a
  mixed-size section. The web repo does not use that editor; fixing it means a `pageSizes` map in `_detail` plus
  `app.html` changes — a separate engine chore.
- `page_size(sheet)` for a sheet outside the index falls back to sheet 0's size (pre-existing); `Book.rects` is only
  called for in-range plans (`_plan_view`, `preview.py:69`), and a plan off the book is `missing`, never cut.
- Rotated pages: `page.rect` already normalises them (story § Out of Scope) — nothing added.
