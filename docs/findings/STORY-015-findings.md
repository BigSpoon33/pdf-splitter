# Findings — STORY-015
**Date:** 2026-09-26
**Status:** done

Commit: `722b231 feat: STORY-015 - home page with chapter and page-range entry points; page-range split mode`
(feature/mvp, pushed to `origin` and `gitea`). Lines below are as of that commit.

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
- **Mode in the URL vs the plan's `source`: both, in that order.** `?mode=ranges` carries the home page's choice
  through the redirect (ADR-009's wording); the first load of a job in that mode saves an empty `ranges` plan, and
  from then on `plan.source` is the truth (robust to a link copied without the query). A chapter job opened with
  `?mode=ranges` appended switches too (its outputs then read "Files from the last cut"); there is no UI to switch
  modes on the job page — the choice is the home page's.
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
`Review.svelte` branches on `editor.plan.source === 'ranges'` (not on the prop) — anything that needs to know the
mode after load should read the plan. `Download.svelte:40-44` still disables Split after a failed cut and says
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
