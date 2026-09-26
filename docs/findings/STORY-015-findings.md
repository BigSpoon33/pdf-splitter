# Findings — STORY-015
**Date:** 2026-09-26
**Status:** done

Commit: `722b231 feat: STORY-015 - home page with chapter and page-range entry points; page-range split mode`
(feature/mvp, pushed to `origin` and `gitea`). Lines below are as of that commit. **Gate r1 failed (1 confirmed,
`STORY-015-review.md`: a link could convert a job) → fixed forward in `fix: STORY-015 - gate r1: the split mode is
fixed at upload, a link can never convert a job` (see § Gate r1 fix at the end; its lines are as of that commit —
the AC-1 mechanism described below is the one it replaced).**

## AC Verification
- [x] AC-1: the home page shows **Split by chapters** and **Split by page ranges**, each with its own drop zone —
  `web/src/components/Home.svelte:16-27` (`oncreated(id, mode?)`; the second zone passes `'ranges'`), mounted by
  `web/src/App.svelte:21` (`navigate(jobPath(id, mode))`). The mode survives the redirect as `/j/<id>?mode=ranges`
  (`web/src/lib/route.ts:28` `jobPath(id, mode?)`, `:16` `parseRoute(path)` now reads the query, `:33` `href()` =
  pathname + search, `navigate`/`onNavigate` compare and report `href()`), and a reload with OR without the query:
  `Review.svelte:113` turns a chapter plan into an empty `ranges` plan on the first load in `?mode=ranges` and
  saves it (`PlanEditor.setRanges`, `web/src/lib/editor.svelte.ts:160`), and from then on `Review.svelte:142`
  `ranges = editor.plan.source === 'ranges'` is the mode. `JobPage.svelte:14` passes `mode` through.
- [x] AC-2: `src/pdf_splitter/models.py:26` `Source` gains `"ranges"`; `:67` `Section.endPage: int | None`
  (`ge=1`, `validate_default=True`), `:84` `_span_in_book`: in ranges mode required, ≤ `pages`, ≥ `page`; outside
  ranges mode any `endPage` is refused (see Decisions); `:162` overrides on a `ranges` plan → 422 `overrides`;
  `:207` `validate_plan` puts `ranges` in the validation context (a nested validator cannot see the plan's source).
  `:102` `Section.dump()` carries `endPage` only where it applies, `Plan.dump()` uses it. Overlaps, gaps and single
  pages are accepted (`tests/test_ranges.py::test_put_plan_accepts_ranges_that_overlap_gap_and_stand_alone`); the
  chapter plan is byte-identical (`::test_chapter_plans_never_carry_end_page`, and every STORY-007..011 test).
- [x] AC-3: `src/pdf_splitter/worker/cut.py:97` `cut_ranges(job_dir, plan, progress)` — `_reset_outputs`, then per
  section `pymupdf.open()` + `insert_pdf(src, from_page=page-1, to_page=endPage-1)` + `save(garbage=4, deflate=True)`
  into `work/<NNN-slug>.pdf` (`engine_name`), rows `{formula, file (= zip_entry), printedPages, pageCount, flags: [],
  notes: [], leaks: [], bytes, index, name}`, `progress(i+1, total, MSG_CUTTING)` per span; `write_zip` unchanged.
  `worker/task.py:96` dispatches on `plan["source"]` (module lookup, so the tests' `task.cut_book` monkeypatch still
  applies). Runs in the same sandboxed task subprocess. pytest: `::test_cut_ranges_copies_each_span_with_its_pages`
  (30-page `text_book`, `1-10, 15-20, 5-7` → 10/6/3 pages, sheets read back from the page text),
  `::test_runner_runs_a_ranges_cut_to_done_with_the_chapter_cuts_zip_shape` (real runner + sandbox, ZIP names,
  manifest keys, progress 3/3), `::test_cut_ranges_resets_stale_outputs_like_the_engine_cut`.
- [x] AC-4: `web/src/lib/ranges.ts` — `parseRanges(text, pages?)` (`:32`, per-token errors: malformed, reversed, 0,
  past the last page; separators `,` `;` newline; `-`/`–`/`—`), `everyN(n, pages)` (`:70`), `formatRanges` (`:78`),
  `rangeName` (`:83`, `Pages 1–10` / `Page 40`), `rangeSections(ranges, previous)` (`:91`, a rename survives when the
  span at that position is unchanged), `spansOf` (`:100`). `web/src/components/RangeEditor.svelte`: the text field
  (`:44` `apply` — the plan follows only an error-free text), the error list (`#ranges-errors`, tokens + a 422 on
  `sections.N.endPage`, `:22` `saveErrors`), "Or split every N pages → Fill the ranges" (`:55` `fill`), and the field
  re-reads the list when a row is deleted below it (`:30-40`). `SectionList.svelte:14` `ranges` prop: rename + delete
  stay; the start-page input, Merge, Add and the preview radio go; each row shows `p. 1–10 · 10 pages`.
  `Review.svelte:213` mounts RangeEditor + SectionList + Download only — no SourcePicker, PagePreview or LayoutPanel
  (`data-mode="ranges"` on `.review`).
- [x] AC-5: `Download.svelte` untouched; the headless run below (PRD AC-14) passed end to end, and the chapter-mode
  Maciocia run is unchanged.
- [x] AC-6: `web/src/lib/ranges.test.ts` (8), `components/RangeEditor.test.ts` (5), `components/Home.test.ts` (2),
  `Review.test.ts` (+3: mode=ranges → one empty-ranges save + no chapter tools; a saved ranges plan opens in range mode
  with rename/delete; a 422 on `endPage` under the field), `JobPage.test.ts` (+3: `?mode=ranges` → save → type →
  Split → files; reload without the query keeps the mode; a chapter job stays one), `route.test.ts` (+7);
  `tests/test_ranges.py` (22). `bun run check` 0 errors 0 warnings; pytest + ruff green.

## Test Results
**Command:** `uv run pytest -q && uv run ruff check`
**Result:** pass
```
367 passed in 81.87s (0:01:21)        (before: 345)
All checks passed!
```
**Command:** `cd web && bun run check && bun run test && bun run build`
**Result:** pass
```
svelte-check: COMPLETED 336 FILES 0 ERRORS 0 WARNINGS
Test Files  20 passed (20)
     Tests  274 passed (274)          (before: 17 files, 246)
dist/assets/index-CBp3zRG6.js   109.19 kB │ gzip: 38.92 kB   (before 102.81 kB / 36.84 kB)
```

**Manual run** (API :8010 + worker + Vite :5181, `PDFSPLIT_JOBS_DIR` in scratch, headless Chromium via Playwright
with `/usr/bin/chromium`; script `ac14.py` in the agent's scratch dir, not the repo):
- Home: two headings, two drop zones (`01-home.png`); `document.title` "PDF Splitter" throughout.
- 30-page `text_book` through the **page ranges** zone → `/j/<id>?mode=ranges`; the range field, Split disabled, no
  Outline radio / Layout / preview. `1-10, 15-2` → live error `15-2 — "15-2" is reversed…`; `1-10, 15-20, 5-7` →
  "3 sections" named `Pages 1–10`, `Pages 15–20`, `Pages 5–7` → Saved → Split → done in 3.3 s → 3 rows; the ZIP
  through the proxy holds `001 - Pages 1–10.pdf` (10 pages, sheets 1–10), `002 - Pages 15–20.pdf` (6, 15–20),
  `003 - Pages 5–7.pdf` (3, 5–7) + `manifest.json`; section 2 download 25,703 B, `Content-Disposition`
  `attachment; filename*=utf-8''002%20-%20Pages%2015%E2%80%9320.pdf`.
- Reload with `?mode=ranges`: field `1-10, 15-20, 5-7`. Reload WITHOUT the query (`/j/<id>`): still range mode,
  same field, files listed.
- "Or split every 10 pages → Fill": field `1-10, 11-20, 21-30`, 3 sections, Split → done in 1.8 s → 3 PDFs of 10
  pages ending on sheets 10/20/30.
- Rename row 1 → "Opening" + Delete row 3: field re-reads `1-10, 11-20`, Saved.
- Chapter mode (Maciocia copy, 1,319 pages) through the **chapters** zone → `/j/<id>` (no query) → Outline radio +
  Layout present, no range field; "Split into 23 PDFs" → done in 16.9 s, 23 rows, 31 badges, ZIP 24 entries /
  53,874,969 B — identical to STORY-011's figures.
- The only console errors are the expected 409 on `GET /manifest` before any cut ("no badges").

## Bugs Found
none

## Decisions
- ~~**Mode in the URL vs the plan's `source`: both, in that order.**~~ **Superseded by the gate r1 fix** (the
  "a chapter job opened with `?mode=ranges` appended switches too" part IS the confirmed finding): the mode is now
  fixed at upload and the URL carries nothing — see § Gate r1 fix. There is still no UI to switch modes on the job
  page; the choice is the home page's.
- **`endPage` outside ranges mode is FORBIDDEN (422 `sections.N.endPage`)**, not ignored: nothing in the chapter SPA
  sends it, and refusing keeps `plan.json` provably free of it. An explicit `endPage: null` on a chapter section is
  accepted as absent (`::test_chapter_plans_never_carry_end_page`). Lax int coercion (`"3"` → 3) is Pydantic's
  default for `page` too, so it is not special-cased.
- **Overrides on a `ranges` plan are refused (422 `overrides`)** as AC-2 says, rather than ignored as ADR-009's text
  says — recorded under ADR-009 "As built".
- **The section-plan preview refuses a `ranges` plan** with 422 `invalid`, loc `["plan", "source"]` (no new error
  code); sheet PNGs still render (nothing asks for them in this mode). The client's `sections` in a preview body are
  validated without the ranges context, so `endPage` there is 422 too.
- **The plan follows only an error-free text.** With any bad token the last good spans stay saved and the errors
  show; good tokens are parsed (and tested) but not applied — otherwise `15-2` on the way to `15-20` would be a save
  and a cuttable list. Retyping the same spans in another spelling is not a save.
- Range names: `rangeSections` keeps a rename when the span at the same position is unchanged; any changed span
  gets `Pages a–b` / `Page n` again. The list's Merge/Add/start-page controls are hidden in range mode (the field is
  the way to change spans).
- `printedPages` in a ranges manifest row is `[page, endPage]` (sheet numbers, ADR-003); the SPA does not read it.

## Handoff Context for Next Session
`Review.svelte` branches on `editor.plan.source === 'ranges'` and nothing else (gate r1: there is no mode prop, no
mode in the URL) — anything that needs to know the mode after load reads the plan; before analysis, the job dir's
`mode.json` (`files.py:read_mode`). `Download.svelte:40-44` still disables Split after a failed cut and says
"Upload the PDF again to retry"; the STORY-012 addendum (recoverable failed cut) changes that line and
`routes/common.py:EDITABLE`. `tests/test_ranges.py` has its own 30-page `ranges_template` session fixture
(`seed_ranges_job`); reuse it for any test that needs a job with more than 6 pages.

## Out-of-Scope Items
- `parseRanges` has no cap on the number of tokens; "every 1 pages" on a 2,000-page book is 2,000 sections =
  `MAX_SECTIONS`, so the API's own limit holds, but a pasted 3,000-token text would be refused by the API
  (`sections` 422 on the list's error line), not by the field.
- A ranges job re-uses the full analyze (index + heading detection) although it needs only the page count — the
  story note said not to fork the upload path; a lighter analyze for `ranges` is a later optimisation.
- The `rate` table, `queue_position` (`status_of` still returns `null`), the janitor and the disk guard are
  STORY-012's (`errors.ts` already carries `rate_limited` and `disk_full`).
- Unbounded output volume (from the gate r1 review, not a 015 defect): 2,000 whole-book spans write ~0.9 GB
  (Maciocia) or ~19 GB (an image-heavy PDF) per cut, in either mode → STORY-012's fourth addendum (output cap).

## Gate r1 fix (2026-09-26)

Review: `docs/findings/STORY-015-review.md` round 1 — 1 confirmed: ANY job opened with `?mode=ranges` (an edited,
already-cut chapter job included) was converted to an empty `ranges` plan and saved, no confirmation, no undo. One
commit on the feature/mvp tip, `fix: STORY-015 - gate r1: the split mode is fixed at upload, a link can never
convert a job`. Lines below are as of that commit.

**Root cause.** The mode lived in the URL: `Review.svelte` (`created.setRanges([])` when the prop said `ranges`
and the plan did not) wrote the plan on load, so a link could rewrite a job.

**Fix — the mode is a property of the job, set at upload (ADR-009 "As built", § API Interface):**
- `POST /api/jobs` takes an optional form field `mode` — `src/pdf_splitter/upload.py:30` `Mode =
  Literal["chapters", "ranges"]`, `:158-163` `create_job(..., mode: Annotated[Mode, Form()] = DEFAULT_MODE)`
  (FastAPI's own 422 `invalid`, loc `body.mode`, for anything else; nothing on disk). `:115` `_accept(file, mode,
  …)` writes the marker at `:141` for a non-default mode only, after the source is in place and before the row
  exists. `src/pdf_splitter/files.py:31-42` `MODE_FILE = "mode.json"`, `DEFAULT_MODE`, `write_mode`, `read_mode`
  (absent = `chapters`, so every pre-fix job and every chapter job reads the same).
- The analyze task writes the first plan FOR the mode: `src/pdf_splitter/worker/task.py:85` `run_analyze` →
  `worker/analyze.py:185` `ranges_plan()` (`{source: "ranges", settings: DEFAULT_SETTINGS, sections: [],
  overrides: {}}`; it is its own PUT-normalized form, so the SPA has nothing to save on load) or `default_plan`
  as before. A ranges job never holds a chapter plan; there is nothing a load could convert.
- SPA: the mode goes with the file — `web/src/lib/api.ts:123` `UploadMode`, `:129` `createJob(file, onProgress?,
  makeXhr?, mode = 'chapters')` appends the `mode` form field (`:157`); `components/DropZone.svelte:6` `Upload`
  type, `:25` `mode` prop, `:52` `upload(file, progress, mode)`; `Home.svelte:19/:24` gives each zone its mode and
  `oncreated(id)` carries only the id; `App.svelte:21` `navigate(jobPath(id))`, `:24` `<JobPage id>` — no mode
  prop anywhere. `Review.svelte` lost the `mode` prop and the `setRanges([])` line; `:137` `ranges =
  editor.plan.source === 'ranges'` is the ONLY mode switch. `lib/route.ts:16` `parseRoute` reads the pathname only
  (a query is tolerated and ignored), `:26` `jobPath(id)`; `JobMode` is gone.

**Tests (all fail on `722b231`, run there in a scratch worktree):**
- `tests/test_ranges.py::test_upload_in_ranges_mode_analyzes_to_an_empty_ranges_plan_then_cuts_ac14` (`mode=ranges`
  → `mode.json` + `source.pdf`, analyze → `GET /plan` == `ranges_plan()` == its own PUT, cut refused 422
  `plan.sections`, then AC-14 through the API: 10/6/3 pages) — on 722b231: `['source.pdf'] == ['mode.json',
  'source.pdf']`; `::test_upload_in_chapter_mode_is_unchanged[default|explicit|empty]` (no marker, plan ==
  `default_plan(analysis)`; an empty `mode=` field is the default, as an HTML form sends it) — passes on both, the
  regression guard; `::test_upload_refuses_an_unknown_mode_and_leaves_nothing_behind[pages|RANGES|chapters,ranges]`
  — on 722b231: `201 == 422`.
- `web/src/App.test.ts` (the whole app on a fetch stub, real router): a `manual` chapter job — renamed, cut — opened
  at `/j/<id>?mode=ranges` → chapter UI, its list and files, zero non-GET requests after `SAVE_DEBOUNCE_MS`, URL
  untouched (on 722b231: a `PUT …/plan` goes out); a `ranges` job opened with `''`, `?mode=ranges`,
  `?mode=chapters` → range mode, zero writes. `Home.test.ts` (each zone's mode reaches the upload; `oncreated(id)`
  only), `DropZone.test.ts` (`mode` prop, `chapters` default), `api.test.ts` (`mode` form field) — on 722b231:
  `undefined`/`null` where the mode should be. `route.test.ts`: `?mode=…` parses to the plain job; `jobPath(id)`.
  `Review.test.ts` / `JobPage.test.ts`: the "mode=ranges converts + saves" cases became "an empty ranges plan from
  the API opens in range mode and saves nothing".

**Counts:** `uv run pytest -q` → 374 passed (was 367), `uv run ruff check` clean; `bun run test` → 280 passed, 21
files (was 274, 20); `bun run check` 0 errors 0 warnings (337 files); `bun run build` 109.05 kB JS (38.87 kB gzip).

**Headless run** (API :8010 + worker, `PDFSPLIT_JOBS_DIR` in scratch, Vite :5181, Chromium `/usr/bin/chromium`;
script `gate_r1.py` in the agent's scratch dir): the ranges zone → redirect to the plain `/j/<id>`; first load shows
the range editor, no chapter tools, `plan.json` is the empty ranges plan, the dir holds `analysis.json mode.json
plan.json source.pdf`, 0 writes; opened again with `?mode=ranges`, without a query and with `?mode=chapters` → range
mode, 0 writes each; `1-10, 15-20, 5-7` → Split → 3 PDFs of sheets 1–10 / 15–20 / 5–7 (writes: one PUT, one POST
cut). The chapters zone → `/j/<id>` with no `mode.json`; section 1 renamed, cut; then opened with `?mode=ranges` and
`?mode=ranges&x=1` → chapter UI, "Renamed by hand" and the files still there, `plan.json` byte-identical, 0 writes.
`POST /api/jobs` with `mode=pages` → 422 `invalid`, `[{'loc': ['body','mode'], 'msg': "Input should be 'chapters'
or 'ranges'", 'type': 'literal_error'}]`. Harness note: reusing ONE headless page for the upload plus three full
navigations made Chromium refuse the fourth document's module loads with `net::ERR_INSUFFICIENT_RESOURCES` (a blank
page; module loads only, never an app error; six navigations in one page without the upload document were clean, and
`--disable-features=BackForwardCache` changed nothing), so the script opens every link in a fresh context.

**Decisions.**
- The marker is a file in the job directory (`mode.json`), not a column: it needs no schema change or migration,
  it dies with the directory, only the analyze task reads it, and a chapter job's directory stays byte-for-byte
  what it was. The status endpoint does not expose it — after analysis the plan's `source` is the mode.
- The URL carries no mode at all (the kickoff allowed a harmless hint): the redirect is `/j/<id>`, the router
  ignores any query, so there is nothing in a link that even looks like it could describe a job.
- `createJob`'s `mode` is its last parameter because `makeXhr` is the tests' seam and `api.test.ts` calls it by
  position; `DropZone` wraps it (`defaultUpload`) so the zones read `upload(file, progress, mode)`.
- An empty `mode=` form value is the default, not a 422: that is what a form with nothing chosen sends and how
  FastAPI reads it; the SPA always sends an explicit value.
