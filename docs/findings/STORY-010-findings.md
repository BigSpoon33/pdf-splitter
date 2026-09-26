# Findings — STORY-010
**Date:** 2026-09-25
**Status:** done

Commit: `e0a10cd feat: STORY-010 - page preview with hatched cuts and draggable cut, gutter and band lines`
(feature/mvp, pushed to `origin` and `gitea`). Lines below are as of that commit. Web only; nothing in
`src/pdf_splitter/`, `tests/` or the engine changed.

**Gate r1 (2026-09-25): 5 confirmed findings (`STORY-010-review.md`), fixed forward in
`fix: STORY-010 - gate r1: preview plans the list on screen, per-sheet geometry, focus on drag, errors scoped to the
selection`** — § Gate r1 fixes below (its lines are as of that commit; it does change the preview route and `tests/`).

## Gate r1 fixes

**F1 + F2 — the preview asked the SERVER's saved list by index while the user saw the LOCAL list.** One root cause:
`POST /sections/{i}/plan` looked `i` up in `plan.json`, so an insert at/above the selection asked for an index the
saved list did not have yet (404 `not_found` → `fail()` marked the editor `gone`, nothing saved after that), and a
delete/merge above it showed the wrong section — and never refreshed, because the dedup key was built from the local
list and did not change when the save landed.
- **Route** (`src/pdf_splitter/routes/preview.py:111` `post_section_plan`): the body may carry `sections`, the list to
  plan `i` in — `PreviewRequest.sections` (`models.py:169`; `list[Section]` under the same validation as PUT /plan:
  page ∈ [1, pages] through the `pages` context, ≤ `MAX_SECTIONS` 2,000, names cleaned, `heading` optional, `extra`
  forbidden) with duplicate names made distinct by the same rule as the saved plan (`dedupe_names`, `models.py:109`,
  now shared by `Plan` — the engine looks entries up by name). Absent → the saved list, as before. `i` past the list
  used → **422 `no_section`** (`errors.py:31`, never the job-level 404). The override's bounds are checked in the
  route once the list is known (`check_override`, `:131`, loc `["body", "override"]`) — the model validator that did
  it before could not know which list `i` named. Nothing is persisted. `run_preview` gets the list; the engine names
  it the same `NNN-slug` way (`engine_entries`).
  Tests: `tests/test_api_e2e.py::test_section_plan_plans_the_list_in_the_body_not_the_saved_one` (:671 — body list ≠
  saved list → the answer follows the body, by index; the inserted section itself; a saved override applies by index
  in whichever list; twins named apart; plan.json byte-identical), `::test_section_plan_validates_index_settings_and_override`
  (:659 — 16 cases now: `3`/`-1`/an empty list/past the body list → 422 `no_section`; page 7, an empty name, an extra
  key, 2,001 sections, an override past the page with a body list → 422 `invalid` with `loc[:2]` = `["body",
  "sections"|"override"]`), `::test_a_section_plan_of_a_body_list_after_a_delete_is_410_not_500` (:706).
- **SPA** (`web/src/components/PagePreview.svelte`): `requestFor` (:63) sends `sections: plan.sections` — the local
  list, always (`SectionPlanRequest.sections`, `api.ts`); the dedup key is exactly `[i, body]` (:102), so a landed
  save asks again only when the body it would send differs from the one last sent (a list that changed under the
  selection, a rename — names are sent — or settings edited elsewhere) and never when the save merely echoed what
  was already previewed; `fail()` (:72) still stops the editor only for `isGone` (job-level 404 `not_found` / 410
  `expired`) — a 422 such as `no_section` is this preview's inline message with a Retry. `errors.ts` gained
  `no_section` (the parity test counts 14 codes).
  Tests (`PagePreview.test.ts`, each fails on `e0a10cd` — the stand-in server `fakeServer` plans the body's list or,
  absent, the list the fake `save` last accepted, and answers the old 404 for an index past it): "an insert above
  the selection previews the section on screen at once and never stops the editor (gate r1 F1)" (:401 — select 3,
  add at sheet 2 → the request is `i = 3` with the 4-section list, "sheets 4–6" stays, no alert, `gone` false, a
  later rename saves), "a delete above the selection previews the section on screen, not the saved list's section at
  that index (gate r1 F2)" (:424 — select 2, delete 1 → `i = 0` with the 2-section list, "sheets 3–4" at once and
  after the save; on `e0a10cd` the fake returns sheets 1–3), "a 422 about the request (no_section) is this preview's
  message with a Retry, never a gone job" (:440), and the reworked "asks again once a save landed with a list or
  settings that differ from what it last sent" (a rename now asks once, after its save; a delete below the selection
  asks once the save lands).
- Live (`b.pdf`, API 8010 + worker, Vite 5192, headless Chromium): select 3 → add "Inserted section" at sheet 2 →
  ONE `POST sections/3/plan` with `sections=1,2,3,4` before the PUT, heading "Section 4: 3 Closing Chapter — sheets
  4–6", no alert, status Saved; a rename after it → PUT lands, names on the server. Select "Chapter Two" (index 2),
  delete section 1 → `POST sections/1/plan sections=2,3,4` at once, "Section 2: … — sheets 3–4" before AND after the
  save (one POST, one PUT).

**F3 — every sheet used the FIRST sheet's size.** `sizeOf(n)` (:188) is now read per sheet in the markup
(`{@const size = sizeOf(n)}`, :381: `aspect-ratio`, `viewBox`, bands, gutter, `aria-valuemax`, the `cutLine`
snippet takes `n` and `size`); the gutter is kept as a FRACTION (`splitFrac`, :193) and becomes an x per sheet
width; a drag records the sheet it started on (`drag.sheet`) and maps the pointer through THAT sheet's frame
(`pointAt(e, size)`, :203; `onDown(kind, sheet)` :226, `onMove` :243, `onKey(kind, sheet)` :277), clamps to that
sheet's height and commits the gutter as `value / size.W`. Test: "every sheet is drawn and dragged in its own size"
(:249, `analysisOf({size})` 522.7 × 789.6 then 700 × 600, `getBoundingClientRect` mocked per sheet from the analysis,
a view on sheets 2–3: viewBox `0 0 700 600` and its aspect on the second frame, gutter x per width, footer band above
each bottom, `aria-valuemax` 789.6/600, the end cut dragged to y = 200 of the 600-pt sheet lands within 2 pt (on
`e0a10cd`: 200 × 789.6/600 ≈ 263), clamps at 600, the gutter dragged on the small sheet → `column_split` 0.5 on both,
a footer drag from the small sheet's bottom, the start cut bounded by the first sheet). Live (`mixed.pdf`, the
reviewer's 522.72 × 789.6 / 700 × 600 book): viewBoxes `0 0 522.7 789.6` and `0 0 700 600`, the PNG's natural aspect
= the shown aspect on both (0.662/0.661, 1.167/1.168); the end cut dragged onto the visible "Beta Chapter" heading
(289.9 pt of the 600-pt sheet) → `endCut: 290` saved; the gutter dragged to the middle of the 700-pt sheet →
`column_split` 0.5, "261.5 pt (50 %)" on the tall sheet and "350 pt (50 %)" on the wide one.

**F4 — `preventDefault()` on pointerdown kept the grip from taking focus.** `onDown` still prevents the default (no
text selection across the page while dragging) and focuses the grip itself (`grip.focus({preventScroll: true})`,
:233), so the arrow keys after a drag nudge THAT line. Test: "a press on a grip focuses it, so the arrow keys after a
drag nudge that line (gate r1 F4)" (:298 — a radio has focus, pointerdown → `defaultPrevented` and
`document.activeElement` is the grip, the drag lands at 380, ArrowUp on the active element → 379; jsdom implements
`SVGElement.focus`). Live: focus "Select section 2" → drag the end cut → focused "End cut of section 2", no text
selection, ArrowDown → 341 saved.

**F5 — `sheetError` was never cleared on a selection change.** The section-plan effect clears `sheetError` (with
`viewError`) whenever it sends a new request (:112) and when the selection goes (:94). Test: "a sheet that failed to
render is forgotten when the selection moves on (gate r1 F5)" (:451 — sheet 4 fails: alert under section 2, none
under section 1 with both images, the alert again back on section 2, none after deselecting). Live: sheet 2
intercepted with a 500 → alert + Retry under section 1, none under section 2 (4 images), the alert again back on 1,
gone after Retry once the sheet serves.

**Counts after the fix:** `bun run test` 190 pass (13 files; +7: 5 gate tests, `no_section`, the reworked refresh
test counts as before) · `bun run check` 320 files 0 errors 0 warnings · `bun run build` 91.96 kB JS (33.32 kB gzip) ·
`uv run pytest` 344 pass (+9: 7 parametrized cases, 2 tests) · `ruff` clean. `document.title` stayed "PDF Splitter";
raw job ids in api.log/worker.log: 0.

## AC Verification
- [x] AC-1: `web/src/components/PagePreview.svelte` (mounted in `Review.svelte:128` between the section list and the
  layout panel). It reads `editor.selected`; on a selection it POSTs the section plan with the LOCAL plan's settings and
  this section's override (`requestFor`, :60 — `override: null` when the plan has none, which is "the engine's own
  plan" for the API) through `getSectionPlan` (`web/src/lib/api.ts:298`, `SectionPlan` :278 typed from
  `tests/test_api_e2e.py::test_section_plan_returns_the_engine_view_with_rects`), then loads `pages[0]` and `pages[1]`
  as PNGs at `PREVIEW_DPI` = 110 (`config.ts:22`) through `getSheet` (`api.ts:319`: a `fetch` into a Blob → object URL,
  revoked when the sheet leaves or the component unmounts, :129 `loadOne` / `dropUrl`), one `<figure>` per distinct
  sheet ("Only sheet n" when first = last). Over each image an `<svg viewBox="0 0 W H" preserveAspectRatio="none">`
  in page points: `.band` rects for `header_band` / `H − footer_band` (:386), the `rects` of the view hatched
  (`<pattern>` per sheet, `.removed`, :389 — drawn only on the sheet each rect names, absolute 1-based per ADR-003),
  the gutter at `column_split × W` (`.gutter`, :430, hidden for `single_column`), and the start cut on the first
  sheet / end cut on the last (`cutLine` snippet :299: the line spans `colSpan(col)` as the engine's `cut_rects` does;
  an absent cut is a dashed line at the sheet's edge labelled "no start/end cut" so it can be added by dragging).
  The view's `flags` show as badges under the heading (`flagLabel`). Sheet PNGs and the section plan carry the
  STORY-007 410 rule: `fail()` (:67) marks the editor `gone` and shows the expired message with no Retry;
  `preview_failed`/network show `messageFor(code)` + Retry (`retry`, :158) and never loop.
- [x] AC-2: every cut grip spans the page width (`cutLine` :311, `x="0"`), so the column is decided by where the
  pointer goes down: `onDown` (:220) records `colAt(x, W, settings)` (`geometry.ts:77`, `full` for `single_column`)
  and the pointer is captured; `onMove` (:232) moves the line locally (`drag.value`, clamped to the page, half
  points); `onUp` (:237) → `commit` (:246) → `editor.setOverride(i, {...override, startCut|endCut: y, startCol|endCol:
  col})` (`editor.svelte.ts:233`: writes `plan.overrides[String(i)]`, deletes the key for `null`/`{}`, then `touch()` →
  the STORY-009 debounced PUT with all its unload handling) and `refresh++` → the section plan is fetched again at
  once with the new override. Tests: `PagePreview.test.ts` "a dragged cut follows the pointer, and the release saves
  the override with the column the drag started in and re-fetches once", "an absent cut becomes one by dragging its
  line; a second drag keeps the other cut's keys", `editor.test.ts` "setOverride writes the section's manual cut…".
- [x] AC-3: the gutter grip (:437, horizontal slider) and the band edges (:400/:418) go through the same drag path;
  `commit` calls `editor.setSetting('column_split', round3(x / W))` (clamped to the API's 0.2–0.8),
  `setSetting('header_band', y)` / `setSetting('footer_band', H − y)` (0–200 pt). The LayoutPanel fields show the
  new values at once (same `editor.plan.settings`). Test: "the gutter and the bands are book-wide settings (AC-3)".
- [x] AC-4: "Reset cut" (:341, shown while `plan.overrides[String(i)]` exists) → `setOverride(i, null)` + a re-fetch
  with `override: null` = the engine's own plan (`::test_section_plan_uses_the_saved_override_unless_told_otherwise`).
  "Remove start/end cut" (:460/:463) sends `{…, startCut: null}` — the explicit null that drops a cut the engine
  found. Badge: `badgesFor(plan, rows, i)` (`web/src/lib/plan.ts:244`) puts `override` ("Manual cut", `.badge.info`)
  first when the PLAN has an override for `i` OR the last cut's row carries the flag, once; `SectionList.svelte:69`
  uses it (the row's `<ul>` is now labelled "Flags of section n"). Tests: `plan.test.ts` "badgesFor…",
  `SectionList.test.ts` "badges a section whose plan has a manual cut, before any cut ran, and drops it when the
  override is reset", `PagePreview.test.ts` "Reset deletes the override and previews the engine's own plan; Remove
  sends an explicit null for one cut".
- [x] AC-5: every grip is `role="slider"`, `tabindex="0"`, `aria-orientation`, `aria-valuemin/max/now` in points and an
  `aria-valuetext` ("379.9 pt, right column", "no start cut (drag or press an arrow key to add one)", "254.5 pt from
  the left (49 %)", "50 pt from the top"); `onKey` (:265) applies `nudgeFor(key, shift, orientation)`
  (`geometry.ts:93`: ±1 pt, ±10 with Shift; Up/Down for cuts and bands, Left/Right for the gutter; the footer band
  grows upward) and commits through the same `commit` — no immediate re-fetch: a run of keystrokes is one debounced
  save, and the landed save (`editor.saves`, `editor.svelte.ts:87`, bumped at :323) triggers one section-plan request.
  `bun run check`: 0 warnings. Test: "arrow keys nudge the focused line by 1 pt, 10 with Shift…".
- [x] AC-6: `web/src/lib/geometry.ts` — `ptToPx`/`pxToPt` (:26/:30), `SheetFrame {W, H, dpi, originX, originY}`
  (:18) with `frameOf` (:34), `pixelSize` (:39, PyMuPDF's rounding), `toPixel`/`toPoint` (:44/:49, origin-aware),
  `pointerToPoint` (:57, a pointer over the scaled image → page points, clamped, dpi-independent), `colAt`/`colSpan`/
  `gutterX`/`halfPoint`/`nudgeFor`. `web/src/lib/geometry.test.ts`: 72/110/48 dpi round trips, the PNG size of
  headed_book's 522.7 × 789.6 page at 72 (523 × 790) and 110 (799 × 1206), a non-zero CropBox origin (20, 30) at 72 and
  110 dpi, pointer mapping over a half-size box, clamping, a zero-size box. Live: the drag in Chromium landed at the
  point values the API echoed back (320 → `endCut: 320`).

## Test Results
**Command:** `cd web && bun run check && bun run test && bun run build`
**Result:** pass
```
svelte-check: COMPLETED 320 FILES 0 ERRORS 0 WARNINGS 0 FILES_WITH_PROBLEMS
vitest:       Test Files  13 passed (13) · Tests  183 passed (183)        (before: 153 in 11 files; +geometry.test.ts 9, +PagePreview.test.ts 11, +10 across api/editor/plan/SectionList/Review)
vite build:   dist/index.html 0.53 kB · index-*.css 9.65 kB (gzip 2.62) · index-*.js 91.81 kB (gzip 33.27)   (before 77.74 / 28.60)
```
**Command:** `uv run pytest -q && uv run ruff check` → `335 passed in 82.43s` (unchanged) · `All checks passed!`

**Manual run** (API `--port 8010` + worker on a scratch `PDFSPLIT_JOBS_DIR`, `API_PORT=8010 bun run dev --port 5181`,
headless Chromium through the Browser skill's playwright — not a repo dependency — driven by a scratch script that
selects, drags with `page.mouse`, presses keys, clicks Reset, and reads `GET /plan` after each step; all three
processes stopped by PID; `document.title` "PDF Splitter" throughout; raw job id in api.log/worker.log: 0):
```
b.pdf (headed_book, 6 sheets), section 2 "Chapter Two" (sheets 3–4)
  1.2s select → "First sheet 3" / "Last sheet 4", sliders: header 50, footer 32, gutter 254.5 pt (49 %), start "no start cut", end 379.9 pt right column
       1 hatched rect (sheet 4 only, below the end cut in the right column) · screenshot: the Closing Chapter heading and below hatched
  1.6s drag the end cut from x = 20 % (left column), 60 pt up → live "320 pt, left column" · release → POST sections/1/plan {override:{endCut:320,endCol:left}}
  3.2s GET /plan overrides {"1":{"endCut":320,"endCol":"left"}} · "Manual cut" badge on row 2 and under the preview heading · "Reset cut" shown
       screenshot: left column hatched below 320, the whole right column hatched (the engine's left-column end rule)
  3.3s focus the end cut, ArrowUp, Shift+ArrowDown → "329 pt, left column" · GET /plan endCut 329 (one PUT, one POST after it landed)
  5.1s drag the gutter to 55 % → "287.5 pt from the left (55 %)" · section plan back in 0.2 s · GET /plan column_split 0.55 · Layout field "55"
  8.3s drag the header edge 20 pt down → GET /plan header_band 70 · Layout field "70"
 10.0s Reset cut → GET /plan overrides {} · end cut "382.9 pt, left column" (the engine's own, under the new gutter) · no badges
       17 API requests: 6 POST sections/1/plan (one per edit), 5 PUT /plan, the 2 sheet PNGs once
Maciocia (1319 sheets, 56.7 MB copy in the scratch dir), outline level 1, section 8 "Note on the translation…" (sheets 22–29)
  1.9s select → both sheets, gutter 261 pt (49 %), start "no start cut", end "no end cut" (the section ends at the bottom of sheet 29)
  2.3s drag the absent end cut up from the sheet's bottom edge in the left column → "629.5 pt, left column" → GET /plan {"7":{"endCut":629.5,"endCol":"left"}}
       screenshot: two-column page, left column hatched below 629.5, right column hatched, "Heading not found on its page" + "Manual cut" badges
  4.1s keys → 638.5 · GET /plan 638.5
  5.8s gutter → 55 % → the section plan came back after 13.8 s (the one-time re-index under new settings, STORY-007 § Handoff) · column_split 0.55
 22.6s header band 70 (another re-index, ~12 s) · 35.0s Reset → overrides {}, "no end cut" again
410 under an open preview (b.pdf): select section 1 → preview up → DELETE /api/jobs/<id> (204) → select section 2 →
  POST sections/1/plan → 410, the two sheet renders that were still out → 410 (STORY-007's "render outlives a delete"):
  alert "This job was deleted (files are kept 24 h)." · 0 Retry buttons · no further requests
```

## Bugs Found
- none in the API. Two things the live run caught in the first draft of the component, both fixed before the commit:
  (1) a cut's grip spanned only its own column, so a right-column cut could not be grabbed from the left to change its
  column (AC-2 says the drag's start decides) — grips now span the page; (2) an absent cut's grip was centred on the
  sheet's edge, half outside the frame, and a click on the edge hit the frame's border — grips are clamped inside the
  page (`clamp(y − 8, 0, H − 16)`).

## Decisions (small ambiguities resolved)
- **Coordinates: origin 0,0 in practice.** PyMuPDF normalizes a CropBox to `page.rect` starting at (0, 0) (checked
  live: `set_cropbox(20, 30, 520, 700)` → `rect (0, 0, 500, 670)`, text bbox and `get_pixmap` in that same space, and
  rotation is folded into `rect` too), and the engine's `W`/`H`, cuts and `rects` all come from that space. So the
  overlay needs no offset; `SheetFrame` carries an origin anyway (AC-6 asks for it) and it is unit-tested. Rotation is
  not exposed by the analysis and needs nothing: `size[n]` is the rotated page.
- **PNG dpi 110** (`PREVIEW_DPI`): the sheets show at ~20 rem, and 72 dpi made body text unreadable when scaled down.
  One render per sheet per settings hash (STORY-007) — ~0.3–0.5 s each on Maciocia.
- **The engine is asked with the LOCAL plan** (settings + override in the request body), never the saved one, so a
  drag shows its result before the 600 ms save lands. But the route plans against the SAVED section list (index `i`
  names a saved section), so the preview also re-asks after a landed save (`editor.saves`) — only when the request
  would differ (`lastKey`, `PagePreview.svelte:95`: index + sections' pages/headings + settings + override). Net:
  one section-plan request per edit; a rename asks nothing; a delete/insert/merge asks once its save has landed.
- **A settings drag (gutter/band) costs a re-index** (12–14 s on Maciocia, the STORY-007 20 s window) because the
  request carries the new settings. The sheets dim (`.updating`) meanwhile; the images do not reload (the pixels do
  not depend on settings, and they were fetched once per sheet).
- **Keyboard nudges do not re-fetch per keystroke** — the debounced save does, once. Pointer releases do (a drag is
  one gesture). Both leave the local line where the user put it (the override is the local truth for the line).
- **"Remove start/end cut"** buttons are the only way to express `{startCut: null}` (drop a cut the engine found) —
  Reset restores the engine's plan, which may include a cut. Small, and it makes the Override contract reachable.
- **`badgesFor` is a union**: the plan's override OR the row's `override` flag, once. A reset override whose file was
  cut under it keeps the badge until the next cut (the file still has the manual cut) — consistent with the other
  manifest badges.
- **A failed request for a newly selected section clears the previous section's sheets** (`viewIndex`, :102) so an
  alert never sits over another section's pages; a re-fetch of the same section keeps the old view (no flicker).
- **No new dependencies.** The preview needs neither a library nor pixel math in the markup.

## Handoff Context for Next Session
`Review.svelte` mounts `PagePreview` with injectable `loadSectionPlan`/`loadSheet` (its `mount()` in `Review.test.ts`
passes fakes and stubs `URL.createObjectURL`, which jsdom lacks). `PlanEditor` gained `setOverride(i, override|null)`
and `saves` (accepted saves; anything that wants to react to "the server has this plan" can watch it — STORY-011's
"editing after a cut returns to review" can key off `saves` + `edited`). The preview owns no unload path: an override
lands in `plan.overrides` on release, so the STORY-009 keepalive/beforeunload handling covers it. Sheet PNGs are
fetched, not `<img src>`-ed: the 410/500 distinction depends on that — keep it if a thumbnails strip is ever added.

## Out-of-Scope Items
- (Gate r1) `README.md:51` still lists `POST /sections/{i}/plan` as `{settings?, override?}` — the README's API table
  is outside this story's authority (`README § Web` only); one line for the orchestrator to align with Architecture
  § API Interface (`sections?`, 422 `no_section`).
- (Gate r1) A rename now costs one section-plan request once its save lands (the list is what the preview sends, names
  included; no re-index — the engine's cache is keyed on settings). If that ever matters, send the list without names
  and let the route name entries by index alone.
- Cut/download/delete/expiry UX (STORY-011). `JobStatus` still does not resume polling after a cut is queued from the
  review page (STORY-009 findings § Out-of-Scope); see KICKOFF-STORY-011 for the two options.
- A thumbnails strip / context sheets before and after the section (story § Out of Scope; the engine's editor has
  them).
- Snapping a cut to a detected heading (the engine editor's `anchorMark`): the analysis exposes heading candidates
  (`y`, `col`, `page`), so a "snap to heading" affordance is possible without an API change — not asked for.
- `#app` is 44 rem wide (`app.css`), so the two sheets show at ~20 rem each; a wider layout for the review page would
  make the preview more comfortable. Not a STORY-010 concern.
- The `PagePreview` re-fetch after a landed save that changed the section list happens even when the selected section
  itself is unchanged (e.g. a section deleted far below it) — cheap (no re-index) and correct, just not minimal.
