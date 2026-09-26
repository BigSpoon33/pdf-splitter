# KICKOFF — STORY-016: Engine: per-sheet geometry for cut rectangles (mixed page sizes) + v0.4.2

## What you're walking into

"pdf-splitter" is a focused public tool: upload a PDF, split it by chapters or page ranges, download, auto-delete.
The web repo never cuts a PDF itself — every chapter cut is the ENGINE's (`monograph-splitter`, pinned by tag). STORY-010's
round-2 review found that the engine computes every redaction rectangle of a section in its FIRST sheet's width/height:
on a section spanning two page sizes the last sheet's rectangles (the gutter at `column_split × W`, the band edges, the
full-width spans) are in the wrong place in the OUTPUT PDF, while the SPA's preview (fixed in STORY-010 gate r1) already
draws per-sheet geometry — preview and output disagree on such books. STORY-016 fixes the engine, releases `v0.4.2`,
and re-pins the web repo. Story file: `docs/stories/STORY-016.md` (AC-1..4).

- **Engine (you write code here):** `~/Documents/Repos/monograph-splitter` (GitHub `BigSpoon33/pdf-splitter-engine`
  = `origin`, Gitea `shuma/monograph-splitter` = `gitea`), branch **`feature/web-mode`**, tip `8e52cc3 fix: STORY-003 -
  gate r1: the excerpt route maps only the name guard to 404 (0.4.1)` = tag `v0.4.1` (annotated, on both remotes;
  `main` is untouched since `6fd22fc`). Baseline: `uv run --group dev pytest -q` → **150 pass** (≈ 6 s). Version lives in
  `pyproject.toml:3` AND `src/monograph_splitter/__init__.py:16` `__version__` (`tests/test_web_mode.py:184-190` pins
  both equal, and `:190` pins the literal `0.4.1` — bump it). `ENGINE_VERSION` (`__init__.py:17`, = 17) is the
  index-cache key: leave it unless an indexing rule changes (it shouldn't — AC-3).
- **Web + API (pin bump only):** `~/Documents/Repos/pdf-splitter` (`origin` GitHub, `gitea` mirror), branch
  **`feature/mvp`**, tip: `7e5af5e feat: STORY-012 - rate limit, disk guard, 24 h janitor and queue position`, its
  docs commit `8689ee5`, the orchestrator's `9435ad4 docs: STORY-012 - gate r1 failed (4 confirmed)…`, then the fix
  `fix: STORY-012 - gate r1: atomic rate window across midnight, output budget fits the sandbox, PUT write race is
  410` (`docs/findings/STORY-012-findings.md` § Gate r1 fixes — the new surface: `Store.take_rate_slot`,
  `ratelimit.take_slot`/`window_hashes`/`utcnow`, `cut.OUTPUT_CEILING`/`ZIP_MARGIN`/`package`/`_within_budget`
  importing `sandbox.FSIZE_BYTES`, `put_plan`'s write → 410), then the gate r2 fix `fix: STORY-012 - gate r2: the
  rate clock is read under the lock; PyMuPDF's file-too-large is the output cap too` (§ Gate r2 fixes:
  `Store.take_rate_slot(window, *, limit, clock=)` reads the clock inside its transaction, `store.utcnow` re-exported
  by `ratelimit`; `cut.hit_file_limit` + `_within_budget` catching MuPDF's `cannot fwrite: File too large`); anything
  after is the orchestrator's gate work (`git log --oneline -8`). Baselines: `uv run pytest -q` → **426 pass** (≈ 90 s),
  `uv run ruff check` clean;
  `cd web && bun run test` → **284 pass** (21 files), `bun run check` 0 errors 0 warnings (337 files), `bun run build`
  ≈ 109.15 kB JS (38.89 kB gzip). `docs/loop-state.json` belongs to the orchestrator: never stage it.
  Two things from the gate that touch the engine bump: the cut task's output ceiling is `sandbox.FSIZE_BYTES − 64 MiB`
  (960 MiB) — a v0.4.2 that writes bigger section files changes nothing here, but a section over 1 GiB would end
  `too_large_output` (the EFBIG mapping — Python's `OSError` and MuPDF's `FzErrorSystem('code=2: cannot fwrite: File
  too large')` alike, `cut.hit_file_limit`), never `resources`; `tests/test_cut.py::test_runner_fails_a_cut_whose_zip_hits_rlimit_fsize_as_too_large_output`
  runs the real task with `cut.write_zip` monkeypatched inside the sandboxed process — a rename of `write_zip` breaks it;
  and `::test_a_section_the_sandbox_refuses_is_too_large_output_on_both_paths` runs `cut_book` under a real
  RLIMIT_FSIZE of half the smallest section of the synthetic book — an engine that writes sections through Python
  instead of MuPDF's `fwrite` still passes (both raise paths are mapped), one that swallows a section's write error
  and reports it as `missing` would not.
- Read, in order: `docs/stories/STORY-016.md`; `docs/findings/STORY-010-review.md:10-16` (the finding, and the SPA's
  per-sheet fix it was paired with); `docs/findings/STORY-003-findings.md` § AC-4 and the "diff gate" paragraphs
  (:30-31 — how the last engine release was gated on the synthetic books and on Maciocia; :5, :11 — how the tags were
  cut); `docs/Architecture.md:57` "Engine additions (monograph-splitter 0.4.0)", ADR-001 (:259-264 — engine changes go
  to the engine repo with tests + diff gate, then a tag bump here); `docs/findings/STORY-012-findings.md` § Handoff.

Toolchain: engine and web-API are Python via `uv` (`uv run --group dev pytest` in the engine; `uv run pytest`, `uv run
ruff check`, `uv lock` in the web repo). SPA is **Bun only** (`bun run check|test|build`). Port 8000 is taken on this
laptop: API on 8010, Vite with `API_PORT=8010 bun run dev --port 5181 --strictPort`. Headless browser only
(`~/Documents/AI/Chrono/skills/Browser/node_modules/playwright` + `chromium` are installed; a `bun run shot.ts`-style
script with `chromium.launch({headless: true})` works).

## What exists today (reuse, don't re-invent)

- **The geometry, all in one place:** `src/monograph_splitter/cuts.py:182` `cut_rects(p, w, h, prof) ->
  [(sheet_index_in_excerpt, (x0, y0, x1, y1))]` takes ONE `w, h` and uses them for sheet 0 (start cut) and sheet
  `last = p["sheet1"] - p["sheet0"]` (end cut): `bottom = h - prof.footer_band`, `split = prof.column_split * w`,
  full-width spans `(0, …, w, …)`. Three callers pass the first sheet's size:
  - `src/monograph_splitter/session.py:166` `Book.rects(p)` — `w, h = self.page_size(p["sheet0"])` (`:142` `page_size(sheet)`
    reads `W`/`H` from the index page, falling back to `doc[0].rect`) — the review UI's / the web preview's rectangles;
  - `src/monograph_splitter/render.py:17` `write_excerpt(book, p, dest, redact, prof)` — `pg0 = out[0]`;
    `cut_rects(p, pg0.rect.width, pg0.rect.height, prof)` then `out[i].add_redact_annot(...)` — the OUTPUT;
  - `src/monograph_splitter/render.py:36` `render_review(...)` — `w, h = doc[0].rect.width, doc[0].rect.height` (the
    CLI's review PNGs; same fix, per sheet drawn).
  `src/monograph_splitter/review/server.py:127,137` call `book.page_size(...)` for the editor's own geometry (the
  web repo does not use the review server; keep it consistent anyway).
- **The index knows every sheet's size:** `src/monograph_splitter/index.py:141-152` stores `"W": round(w, 1), "H": …`
  per page; `detect.py:222` collects `(page.rect.width, page.rect.height, lines)` per sheet. So `Book` can answer a
  size per sheet without opening pages — `page_size(sheet)` already does, for one sheet.
- **The plan `p`:** `{sheet0, sheet1, startCut, startCol, startBand?, endCut, endCol, endBand?, kind, flags}`; the
  start geometry belongs to `sheet0`, the end geometry to `sheet1`. `cuts.py:182`'s docstring explains the band logic.
  The SPA side of the same contract: `web/src/components/PagePreview.svelte:187-198` (`sizeOf(n)` = `analysis.size[n-1]`,
  `rectsOn(n)`), and the preview route returns the engine's `_plan_view` with `rects: [[sheet, [x0,y0,x1,y1]]]`
  (`tests/test_api_e2e.py::test_section_plan_returns_the_engine_view_with_rects`, `SECTION_PLAN_KEYS`).
- **Synthetic books:** engine `tests/fixtures.py:13` `W, H = 522.72, 789.6`, `FakeBook` (`:19`), `scenario`/`heading_book`
  fixtures used by `tests/test_cuts.py` (`::test_redaction_removes_the_neighbours_text_and_keeps_ours` :77 is the
  pattern for asserting on extracted text positions after a real redaction; `::test_verify_flags_a_leak_and_a_dropped_tail`
  :92 for `verify`). The web repo has its own port (`pdf-splitter/tests/fixtures/books.py:headed_book`, `text_book`)
  and its `analyzed_template` fixture — you may add a two-size book there for AC-4's manual check, but the engine
  test is the contract.
- **Diff gate:** the engine ships `monograph-splitter-diff` (`pyproject.toml:22`, `diff_manifest.py`) comparing two
  output dirs' `manifest.json`; STORY-003 ran it on `scenario_book` + `tests/profile-test.toml` and `heading_book` +
  `tests/profile-headings.toml` three ways each (`--verify`, `--verify --no-redact`, `--verify --limit 2 --only …`),
  pre-change in a `git worktree` of the previous tag, post-change in the working tree, and on Maciocia *Foundations*
  (`--profile maciocia-foundations`, entries JSON from Inkwell's `extract_pattern_pdfs.build_entries()`; 211 excerpts,
  `bytes` differs by a few bytes on ~4 rows from MuPDF nondeterminism — not a change). The real books live in
  Inkwell's `Books/` (READ-ONLY: `~/Documents/AI/Inkwell` may only be read, never written; the STORY-003 findings name
  the exact paths). Uniform-size books must produce byte-identical DECISIONS (the manifest rows minus `bytes`).
- **Web repo pin:** `pdf-splitter/pyproject.toml:12` `monograph-splitter @ git+https://github.com/BigSpoon33/pdf-splitter-engine@v0.4.1`
  → `@v0.4.2`, then `uv lock` (+ `uv sync`); `tests/test_health.py:27` pins `engine_version == "0.4.1"`; Architecture
  mentions `v0.4.1` at `:28`, `:263` (ADR-001) and `:344` (Dependency Map). `docs/Architecture.md:57` "Engine additions
  (monograph-splitter 0.4.0)" is where a one-line "0.4.2: per-sheet geometry" note belongs.

## Contracts (the tests ARE the contract — never hand-write sample JSON)

- Engine plan/rect shape: `tests/test_cuts.py` (all), `tests/test_web_mode.py` (`SUMMARY_KEYS` :33, the `_plan_view`
  and `rects` tests, the version pin :184-190).
- Engine diff gate: `monograph-splitter-diff <pre> <post>` → "0 changed" on every synthetic run and on Maciocia
  (STORY-003 findings :30-31 are the worked example, including the `bytes` caveat).
- Web preview ↔ output: `pdf-splitter/tests/test_api_e2e.py::test_section_plan_returns_the_engine_view_with_rects`,
  `::test_section_plan_uses_the_saved_override_unless_told_otherwise`; `tests/test_cut.py::test_cut_book_writes_every_section_and_translates_overrides`,
  `::test_runner_runs_the_real_cut_task_to_done`; `web/src/components/PagePreview.test.ts` (per-sheet hatch, from STORY-010 r1).
- Web health/version: `tests/test_health.py::test_health_shape_and_jobs_dir_created`.

## Critical gotchas

1. **Two sheets, two sizes, one plan.** The start cut's rectangles are on sheet 0 of the excerpt (= book sheet
   `sheet0`), the end cut's on sheet `last` (= `sheet1`). `cut_rects` must take the size of the sheet each rectangle
   lands on — the cleanest shape is `cut_rects(p, sizes, prof)` where `sizes(i)` (or a list) gives `(w, h)` per excerpt
   sheet, with the old `(p, w, h, prof)` call still working for uniform books so the diff gate stays 0. Every rectangle
   with `w`, `split` (= `column_split × w`), `bottom` (= `h − footer_band`) or `prof.subheader_bottom`/`redact_top`
   (absolute points — unchanged) needs the right sheet's `w, h`.
2. **The excerpt's pages ARE the book's pages** (`write_excerpt` does `insert_pdf(from_page=sheet0, to_page=sheet1)`),
   so `out[i].rect` is sheet `sheet0 + i`'s size; `Book.page_size(sheet0 + i)` gives the same from the index. Prefer
   the index in `Book.rects` (no page open) and the page rect in `write_excerpt` (already open), and assert in the
   test that both agree.
3. **Rotated pages:** out of scope (story § Out of Scope) — `page.rect` already normalises rotation; do not add
   handling.
4. **`ENGINE_VERSION` stays 17** unless the INDEX changes; per-sheet cut geometry is not an indexing rule. AC-3 says
   so explicitly; Inkwell's cached indexes must not re-index.
5. **Never move `v0.4.1`.** Tag `v0.4.2` (annotated) on the new tip of `feature/web-mode`, push the branch AND the
   tag to `origin` and `gitea` (`git push origin feature/web-mode v0.4.2 && git push gitea feature/web-mode v0.4.2`),
   check `git ls-remote --tags origin` shows it, THEN bump the web repo's pin. `uv lock` in the web repo needs the
   network (a stopping condition if it is unavailable).
6. **The web suite is the second gate:** after the pin, `uv run pytest -q` (411 + whatever you add) must be green —
   `test_health.py:27` will fail until it says `0.4.2`.
7. **Read-only Inkwell.** The Maciocia / Chen & Chen PDFs for the diff gate are read from `~/Documents/AI/Inkwell`;
   outputs go to a scratch dir (the scratchpad or `/tmp`, kept small and deleted after — /tmp is RAM-backed).
8. **Process safety:** stop servers by PID (`lsof -ti :8010`); `pgrep -f "pdf-splitter worker"` matches the shell
   running your own command — list with `ps -eo pid,args | grep "[v]env/bin/pdf-splitter"` and kill explicit PIDs,
   never `pkill -f`/`killall`.
9. **Bun/uv only; commit messages end with the Co-Authored-By line; stage explicit paths (never `git add -A`/`.`;
   never `docs/loop-state.json`); never `reset --hard` / `checkout .`.**

## Recommended AC ordering

1. AC-1 (engine): make `cut_rects` per-sheet (`cuts.py:182`); thread sizes through `Book.rects` (`session.py:166`,
   using `page_size(sheet0 + i)`), `write_excerpt` (`render.py:17`, using `out[i].rect`) and `render_review`
   (`render.py:36`); the review server's two `page_size` calls (`review/server.py:127,137`) if they feed rectangles.
2. AC-2 (engine test): `tests/test_mixed_sizes.py` + a two-size book in `tests/fixtures.py` (522.72×789.6 then
   700×600) with one section spanning both and an end cut in a column on the second size; assert on the written
   excerpt's LAST page text positions (the kept column's lines present, the other column's gone — the
   `test_redaction_removes_the_neighbours_text_and_keeps_ours` pattern) and `verify` → 0 leaks; assert `Book.rects`
   equals what `write_excerpt` applied (per-sheet `w, h`).
3. AC-3 (diff gate): synthetic runs (3 ways × 2 books, pre = `git worktree add … v0.4.1`) and Maciocia + Chen & Chen
   through the CLI; `monograph-splitter-diff` 0 changed; `ENGINE_VERSION` untouched. Record the exact commands and
   counts in the findings.
4. AC-4 (release + pin): version `0.4.2` in `pyproject.toml`, `uv.lock`, `__init__.py`, `tests/test_web_mode.py:190`;
   commit `fix: STORY-016 - cut rectangles use each sheet's own size (0.4.2)`; tag; push branch + tag to both remotes.
   Then in the web repo: pin `@v0.4.2`, `uv lock`, `uv sync`, `tests/test_health.py:27` → `0.4.2`, Architecture
   `:28`/`:263`/`:344` + a line under `:57`; `uv run pytest -q`, `uv run ruff check`, `cd web && bun run check && bun run
   test && bun run build`; the manual check: a mixed-size PDF uploaded through the SPA (API :8010, Vite :5181,
   headless Chromium), the section preview's hatch vs the downloaded section (open it with PyMuPDF, compare the
   redacted region to the preview's `rects`) — screenshot kept small, path named in the findings.

## Conventions

- Engine: the module style of `cuts.py`/`session.py` (plain functions, dict plans, docstrings that explain the
  layout rule). Web: FastAPI/Pydantic style, TypeScript strict, Svelte 5 runes. Comments say WHY, never WHAT.
- Commits: engine `fix: STORY-016 - cut rectangles use each sheet's own size (0.4.2)` on `feature/web-mode`; web
  `chore: STORY-016 - pin monograph-splitter v0.4.2` on `feature/mvp`; each ending with
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Stage explicit paths only.
- Push: engine `git push origin feature/web-mode v0.4.2` + `git push gitea feature/web-mode v0.4.2`; web
  `git push origin feature/mvp` + `git push gitea feature/mvp`.
- Findings + next kickoff: `docs/findings/STORY-016-findings.md`, `docs/KICKOFF-STORY-013.md` (the loop queue is
  STORY-016 → 013; STORY-014 is held), committed in the web repo as `docs: STORY-016 - findings + KICKOFF-STORY-013`;
  set STORY-016's status lines to Done.

## Authority

- Free: engine `src/monograph_splitter/{cuts,session,render}.py`, `review/server.py` (geometry calls only),
  `tests/test_mixed_sizes.py` (new), `tests/fixtures.py` (add a book, don't change existing ones), the version
  files; web `pyproject.toml`, `uv.lock`, `tests/test_health.py:27`, `docs/Architecture.md` (the four version
  mentions + one "as built" line), `docs/findings/`, `KICKOFF-*`, STORY-016's status lines.
- Do not touch: `ENGINE_VERSION`, `index.py`/`detect.py` (indexing rules), existing tests' assertions (add, don't
  rewrite), the web repo's SPA/API code (STORY-010 already did the preview side), `~/Documents/AI/Inkwell`
  (read-only), `~/Documents/Vaults`, `docs/loop-state.json`, the PRD, `main` in either repo, tag `v0.4.1`.
- Out of scope: rotated pages beyond `page.rect`; any change to how the SPA draws; STORY-013 (Caddy/compose);
  STORY-014 (terms/privacy).

## Stopping conditions (BLOCKED protocol)

- A per-sheet cut changes decisions on a uniform-size book (the diff gate is not 0) and you can't see why — do not
  "fix" the gate; report.
- A pre-existing test (engine or web) fails for reasons unrelated to your change.
- `uv lock`/`uv sync` or the tag push needs the network and it is unavailable.
- The real books for the diff gate are not readable where STORY-003's findings say they are.

## Final report shape

Per-AC ✅/❌ with file:line (engine and web); counts (engine `uv run --group dev pytest` before 150 / after N; web
`uv run pytest` before 411 / after N; `bun run test` 284 / N); `bun run check` and `bun run build`; the diff gate's
numbers (synthetic × 6, Maciocia, Chen & Chen); the release (tag `v0.4.2` sha on both remotes, `v0.4.1` unmoved); the
web pin (`pyproject.toml`, `uv.lock` resolved sha, health says `0.4.2`); the manual mixed-size check (what was compared,
the screenshot's path); the commits on both repos and both remotes; decisions taken (how sizes are threaded, what
happens to a plan whose sheets are out of the index's range); what STORY-013 should know.

## Previous attempt (RETRY — read this first)

Attempt 1 (engine `116a4bb` = v0.4.2; web `c410caa`, `a9932a9`) passed everything except ONE test gap —
`docs/findings/STORY-016-review.md`. Engine repo, branch feature/web-mode, one TEST-ONLY commit:
`test: STORY-016 - review PNGs hatch each sheet in its own size`
Make `test_review_pngs_are_drawn_in_each_sheets_size` (or a new test) assert WHERE the hatching lands on the
last-sheet PNG of the AC-2 mixed fixture (e.g. sample pixels / record `draw_rect` calls: the hatch covers
x 340.9–700 of the 700×600 sheet and does NOT cover Alpha's kept tail). Prove it fails with render.py:43
(and separately :48) reverted to sheet-0 geometry in a scratch copy; restore byte-for-byte. No src change,
NO new tag (v0.4.2 ships the same code). Push the branch to origin + gitea. Note it in
docs/findings/STORY-016-findings.md (web repo, feature/mvp) under "Gate r1".
