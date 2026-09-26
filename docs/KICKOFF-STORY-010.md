# KICKOFF — STORY-010: SPA: page preview with cut overlays and draggable cuts

## What you're walking into

"pdf-splitter" is a public site: someone drops in a big PDF and gets back one PDF per chapter or section.
The product lives in two repos:

- **Web (you write code here):** `~/Documents/Repos/pdf-splitter` (GitHub `BigSpoon33/pdf-splitter` = `origin`,
  Gitea mirror = `gitea`), branch **`feature/mvp`**. Stay on that branch. STORY-009 landed as
  `784b31d feat: STORY-009 - source picker, editable section list and layout panel`, its docs commit
  `955ab62`, then the gate r1 fix `9e29032 fix: STORY-009 - gate r1: overrides follow boundaries, undo/paste/picker
  state, saves survive leaving, badges after reload` and its docs commit `docs: STORY-009 - gate r1 fixes in findings
  + KICKOFF-STORY-010`, then the gate r2 fix `fix: STORY-009 - gate r2: large plans save on tab switch and are never
  silently lost on close`. Anything after those is the orchestrator's gate work; check `git log --oneline -8`.
  **Baselines:** `cd web && bun run test` → **153 pass** (11 files, ≈ 3 s), `bun run check` 0 errors 0 warnings
  (316 files), `bun run build` ≈ 78 kB JS (28.6 kB gzip); `uv run pytest` → **335 pass** (≈ 75 s), `uv run ruff check`
  clean. `docs/loop-state.json` belongs to the orchestrator: never stage it. Line numbers below are as of `9e29032`
  except where the gate r2 fix moved them (`editor.svelte.ts`: see its § in the findings).
- **Engine (read-only):** `~/Documents/Repos/monograph-splitter`, pinned at `v0.4.1`. Its review editor
  `src/monograph_splitter/review/app.html` is the prior art for hatching, ruler and band semantics — port the math,
  not the code (story note).
- Read, in order: `docs/stories/STORY-010.md` (its ACs are authoritative), `docs/Architecture.md` § web (SPA),
  § Data Types (Section plan, Plan `overrides`), § API Interface, ADR-003 (sheet numbers), then
  `docs/findings/STORY-009-findings.md` (§ Gate r2 fix, § Gate r1 fixes, § Decisions, § Handoff, § Out-of-Scope) and
  `docs/findings/STORY-007-findings.md` AC-2/AC-3 (the preview subprocess and its 410 rules).

Toolchain: the SPA is **Bun only** (`bun install` / `bun run dev|check|test|build`, `bunx`; never npm/npx/yarn/pnpm).
`uv` only to run the API. Port 8000 is taken on this laptop: run the API on 8010 and Vite with `API_PORT=8010 bun run dev`.

## What STORY-009 established (use these, don't re-invent)

- **The page:** `web/src/components/JobPage.svelte` renders `JobStatus` (which now takes `onstatus`,
  `JobStatus.svelte:11`) and mounts `Review.svelte` with `jobState: 'review' | 'done'` once the job is there
  (`JobPage.svelte:10-15`). `Review` loads `getAnalysis` + `getPlan` and, in parallel, `getManifest` (always; a 409
  before any cut = no badges, `Review.svelte:55`), builds ONE `PlanEditor` (`:64`, with `initialPicker(analysis)`),
  fills `editor.rows` from the manifest and renders `SourcePicker`, `SectionList`, `LayoutPanel`, the save-state line
  (+ the "cut again" note when a manifest exists and the plan moved on, `stale` :90) and the Undo toast. **PagePreview
  mounts inside `Review.svelte`** next to `SectionList` — it needs `editor`, `analysis` (sizes, pages) and the job `id`.
  `Review`'s effect cleanup calls `editor.destroy()` — keep that: `destroy()` is what sends a pending save when the
  user leaves (gate r1 F7).
- **The plan store** `web/src/lib/editor.svelte.ts` `PlanEditor` (a rune class; every field is `$state`):
  `plan: Plan` (local truth), `selected: number | null` (:81 — **the selected section lives here**; `select(i)` :208,
  kept in step by delete/merge/add), `picker: PickerState` (:66, the SourcePicker controls — snapshotted with the
  list so Undo restores them), `rows: ManifestRow[]` (:68, the last cut's manifest; `merge` drops the absorbed row),
  `draft` (:83, a section name being typed: `setDraft` :159 / `commitDraft` :164 — the plan gets it on
  blur/Enter/leaving), `dirty`/`edited`/`saving`/`error`/`fieldErrors`/`gone`, and the edit methods
  `replaceSections(source, sections, label, picker)` :121, `rename` :151, `setPage` :171, `remove` :178,
  `merge` :185, `add` :195, `setSetting(key, value)` :203 (**a gutter drag = `setSetting('column_split', x / W)`, a
  band drag = `setSetting('header_band' | 'footer_band', pt)`**), `errorAt(...loc)` :214 (a 422 message for a
  control), `flush()` :253 (send now), `destroy()` :325 (commits the draft, sends what is pending, unbinds the
  `pagehide`/`visibilitychange`/`beforeunload` listeners the constructor added — `UnloadSource` :25; tests pass a
  stand-in `page` whose `fire(type, event?)` passes an event with `preventDefault`).
  Every edit goes through the private `touch()` → a debounced PUT (600 ms, one in flight, latest after; the 200 body
  is adopted unless an edit happened meanwhile). Leaving (gate r2): a hidden tab flushes an ORDINARY PUT; `pagehide`
  sends at once with `keepalive` only when `fitsKeepalive(plan)` (`api.ts:243`, ≤ `KEEPALIVE_MAX_BYTES` 60,000 —
  Chromium refuses bigger keepalive bodies), else best-effort; `beforeunload` → `preventDefault()` while `dirty`, so
  the browser prompts before a large plan could be lost; a failed send leaves the edit pending for the next flush.
  Don't add a second `beforeunload`/keepalive path in the preview — an override edit through `touch()` inherits all
  of it. The save callback is `SavePlan = (plan, opts?: {keepalive?}) => Promise<Plan>`.
  **There is no override method yet:** add `setOverride(i, override | null)` (write `plan.overrides[String(i)]` or
  delete the key, then `touch()`); keep the re-keying in `web/src/lib/plan.ts` (`shiftOverrides` :147,
  `removeSection` :168, `mergeWithNext` :178, `insertSection` :196, `dropEnd` :160 — **since gate r1 F2 the section
  whose END moved loses its end override**: delete/insert/merge all apply it) intact — overrides are keyed by section
  INDEX and the API refuses keys past the end. A preview that shows an override's cut must therefore re-fetch the
  section plan after any delete/insert/merge, not only after its own drag.
- **Types and client** `web/src/lib/api.ts`: `Analysis` (:174; `size[{W,H}]` per sheet, `pageLabels`), `Plan` (:208),
  `Override` (:201 — only the keys sent are applied: `startCut: null` REMOVES a cut, an absent key keeps the engine's;
  `startCol`/`endCol` ∈ `full|left|right`), `Section`, `PlanSettings` (:184, incl. optional `heading_wrap_gap`),
  `ManifestRow` (:218), `getAnalysis` :228, `getPlan` :232, `putPlan(id, plan, {signal?, keepalive?})` :248
  (`SaveOptions` :236, `fitsKeepalive` :243), `getManifest` :258, all through the private `request()`; `isGone(err)` for 404/410. **Add `getSectionPlan(id, i, body?)` and a `sheetUrl(id, n, dpi)`** on top
  (a PNG is an `<img src>`, not a fetch — but a 410 on it needs handling: see gotcha 3).
- **Pure helpers** `web/src/lib/plan.ts`: `badgeFlags`/`rowFor`/`flagLabel` (the flag badges; `SectionList` reads
  `editor.rows`, it takes no `rows` prop), `parseList`, sources (`headingSections(analysis, {level, threshold,
  maxLength?, bands?})` :64 — the PRD scope-2 filters; `PickerState` :31, `initialPicker` :41).
  `web/src/lib/config.ts`: `SAVE_DEBOUNCE_MS` 600, `UNDO_MS` 8000, `POLL_MS` 1500.
- **CSS:** tokens on `:root` in `web/src/app.css` (`--bg --surface --fg --muted --border --accent --accent-soft
  --danger --danger-soft --ok --radius --focus`), global `.card .error .bar .badge .toast .field .field-error`,
  `button.primary`, form controls styled globally. `.row.selected` in `SectionList.svelte` highlights the selection.
- **Tests:** vitest + @testing-library/svelte (jsdom). Fixtures for the analysis/plan/manifest are in
  `web/src/lib/fixtures.ts` (`analysisOf`, `planOf`, `sectionsOf`, `rowOf`) — extend them, don't fork them. Loaders
  are injected as props (`Review.test.ts` `mount()`, which passes `jobState` and a `loadManifest` — `noCut` rejects
  409, `cut` resolves rows); `PlanEditor` takes a fake `save` (`editor.test.ts` `deferredSave`) and a fake `page`
  (`fakePage()`) so no test binds real window listeners it cannot fire. `SectionList.test.ts` sets `editor.rows`
  directly. With fake timers, don't `vi.waitFor` timing assertions (see `settle()` in `JobStatus.test.ts`). A name
  input holds a DRAFT until blur/Enter: a test that renames through the DOM must `fireEvent.blur` (or Enter) before
  it expects a save.

## API contracts you'll use (the tests ARE the contract — never hand-write sample JSON)

- `GET /api/jobs/{id}/sheets/{n}.png?dpi=48|72|110` — `tests/test_api_e2e.py::test_sheet_png_renders_through_the_sandboxed_subprocess_and_caches`
  (:373; `image/png`, `Cache-Control: private`, one render then cached per (sheet, dpi, saved-settings hash) —
  `::test_sheet_png_cache_key_follows_the_saved_settings` :408: **a settings change re-renders every sheet once**),
  `::test_sheet_png_rejects_other_dpis` :417 (422), `::test_sheet_png_validates_the_sheet_against_the_job` :425 (404
  past `pages`), `::test_preview_failures_are_500_preview_failed_and_logged_without_the_id` :432. `n` is the 1-based
  sheet (ADR-003). The PNG's pixel size is `W × dpi/72` by `H × dpi/72` of `analysis.size[n-1]` — AC-6's conversion
  (also a non-zero CropBox origin: the engine's coordinates are page-space points; check how `render_sheet` in
  `src/pdf_splitter/preview.py` sets the clip before assuming origin 0,0).
- `POST /api/jobs/{id}/sections/{i}/plan` `{settings?, override?}` → `{pages:[a,b], startCut, startCol, endCut,
  endCol, flags, notes, rects:[[sheet,[x0,y0,x1,y1]]]}` — `::test_section_plan_returns_the_engine_view_with_rects`
  :584 (**`rects` name the ABSOLUTE 1-based sheet**, `y0 == endCut` on the end sheet; an `override` in the body is
  applied and persisted nowhere), `::test_section_plan_uses_the_saved_override_unless_told_otherwise` :613 (absent
  `override` = the saved one; explicit `null` = the engine's own plan — that is what **Reset (AC-4)** previews
  before you delete the key), `::test_section_plan_under_other_settings` :627 (`single_column` → full-width cuts),
  `::test_section_plan_validates_index_settings_and_override` :651 (9 cases: `i` out of range 404, bad settings/override
  422 with `errors[].loc`). Nothing is persisted by the preview: **saving the override is your `PUT /plan`** through
  the editor (`::test_put_plan_normalizes_names_and_returns_the_saved_plan` shows the override round trip; the bounds
  `startCut ≤ H` of the section's first sheet, `endCut ≤ max(H)` → 422 `["body","overrides"]`,
  `::test_put_plan_rejects_bad_plans_with_field_errors` :238).
- **410 during a render** — `::test_a_sheet_render_that_outlives_a_delete_is_410_and_leaves_no_directory` :487,
  `::test_a_section_plan_whose_engine_outlives_a_delete_is_410_and_leaves_no_directory` :500,
  `::test_a_sheet_deleted_after_its_render_is_410_not_500` :510, `::test_a_sheet_deleted_between_its_render_and_the_read_is_410_not_500` :526,
  `::test_a_sheet_deleted_after_the_row_check_is_served_not_500` :546, `::test_a_section_plan_after_a_delete_is_410_not_500` :565:
  a preview can answer 410 even when the job was live when the request went out. Treat 410 from ANY preview as
  "the job is gone" (`isGone`), never as a preview failure; 500 `preview_failed` is the expected failure (show
  `messageFor('preview_failed')`, offer a retry, don't loop).
- `GET /api/jobs/{id}/manifest` — `::test_manifest_serves_the_last_cuts_rows_from_the_zip` :698 (already consumed
  by `SectionList`).

## Critical gotchas

1. **Ports.** `PDFSPLIT_JOBS_DIR=<scratch> uv run pdf-splitter api --port 8010` + `uv run pdf-splitter worker` (same
   env), then `cd web && API_PORT=8010 bun run dev`. Stop what you start by PID (`lsof -ti :8010`, `kill <pid>`),
   never `pkill -f`.
2. **A settings change makes the FIRST section-plan preview re-index the book** inside the API's 20 s window
   (12–14 s for 1319 pages here; STORY-007 findings § Handoff): a gutter/band drag that saves `column_split` and
   then re-fetches the section plan will feel slow once, then be fast. Sheet PNGs never re-index but DO re-render
   (new cache key). Don't fetch a section plan on every pointer-move: draw the overlay locally while dragging and
   re-fetch on release (AC-2 says exactly that).
3. **`<img src=/api/jobs/…/sheets/n.png>` can't tell 410 from 500 from a network drop.** Either fetch the PNG with
   `request()`-style error handling into an object URL (and revoke it), or probe with a `HEAD`/the section plan
   first. The id stays in the URL only (ADR-007): never in logs, storage or `document.title`; object URLs are fine.
4. **Coordinates (AC-6).** PDF points, origin top-left as the engine's `rects` and cuts use them (`y0 == endCut`,
   `::test_section_plan_returns_the_engine_view_with_rects`); PNG pixels = points × dpi / 72. Put the conversion in
   `web/src/lib/geometry.ts` (px↔pt, dpi 72/110, CropBox origin) and unit-test it — the story's E2E table names
   `web/src/lib/geometry.test.ts`.
5. **Override semantics.** `Override` keys are applied only when present: `{startCut: null}` REMOVES the engine's
   start cut, `{}` keeps everything; the column the drag started in sets `startCol`/`endCol` (AC-2). A section's
   override survives merge/delete only as `plan.ts` re-keys it (`mergeWithNext` keeps i's start + i+1's end).
   **Reset (AC-4)** = delete `plan.overrides[String(i)]` (then the section plan preview with `override: null`
   shows the engine's own cuts). "Sections with overrides show a badge": `SectionList.svelte` already badges
   manifest flags — an `override` badge from the PLAN (not the manifest) is a small addition there
   (`class="badge info"` exists).
6. **Debounce vs drag:** `editor.setSetting` and a future `setOverride` go through the 600 ms debounce; a drag's
   release should call them once, not per frame. Do NOT hold a drag's value outside the editor the way name drafts
   are held (`editor.draft`): a pending cut must be in `plan.overrides` before the tab hides, or `sendBeforeUnload`
   sends the plan without it.
7. **Keyboard (AC-5):** the cut line is a focusable element (`tabindex=0`, `role=slider` with `aria-valuenow`/min/max
   in pt, `aria-label`), arrows nudge 1 pt, Shift+arrows 10 pt; `bun run check` must stay at **0 warnings** — a11y
   warnings count (a `<div onmousedown>` without a role/keyboard handler is one). Svelte 5 only (runes, snippets).
8. **Tests must not need the network or the API**: inject loaders as props (`Review.test.ts` pattern) or
   `vi.stubGlobal('fetch', …)` (`api.test.ts`).
9. **Stay out of the Python side** unless an AC is impossible without it (then: findings + BLOCKED protocol). The
   preview routes are done; STORY-009's manifest route and wrap gap were orchestrator decisions.

## Recommended ordering

1. `web/src/lib/geometry.ts` + `geometry.test.ts` (AC-6): `ptToPx(pt, dpi)`, `pxToPt(px, dpi)`, a `SheetFrame`
   `{W, H, dpi, originX, originY}` mapping page points ↔ image pixels; the `size` of `analysisOf()` in
   `web/src/lib/fixtures.ts` is 522.7 × 789.6.
2. `web/src/lib/api.ts`: `getSectionPlan(id, i, body?)` (+ `SectionPlan` type from `::test_section_plan_returns_the_engine_view_with_rects`),
   `sheetUrl`/a PNG loader; `api.test.ts`.
3. `PlanEditor.setOverride(i, override | null)` (+ `editor.test.ts`); an `override` badge in `SectionList`.
4. `web/src/components/PagePreview.svelte` (AC-1): reads `editor.selected`, fetches the section plan, shows the first
   and last sheet PNGs (`pages[0]`, `pages[1]`) with an inline SVG overlay in **points** (`viewBox="0 0 W H"`, the
   image scaled to fit — no pixel math in the markup): gutter line at `column_split × W`, header/footer bands,
   hatched `rects`, start/end cut lines. Mount it in `Review.svelte`.
5. Dragging (AC-2/AC-3): pointer events on the cut lines, gutter and bands; local overlay while dragging, on release
   `setOverride` / `setSetting` and re-fetch the section plan. Keyboard nudges (AC-5). Reset (AC-4).
6. `cd web && bun run check && bun run test && bun run build`, then the manual run (API on 8010 + worker;
   `PYTHONPATH=tests uv run python -c "from fixtures.books import headed_book; headed_book('b.pdf')"` for a quick book,
   the Maciocia copy in a scratch dir for a real two-column one — never write next to the original).

## Conventions

- TypeScript strict, Svelte 5 runes, small components, plain CSS on the tokens. Comments say WHY, never WHAT.
  `bun run check` clean (0 warnings).
- Commit on `feature/mvp`: `feat: STORY-010 - page preview with hatched cuts and draggable cut, gutter and band lines`,
  ending with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Stage explicit paths only
  (never `git add -A` / `.`; never `docs/loop-state.json`). Never `reset --hard` / `checkout .`.
- New dependencies: avoid; if one is unavoidable, from the official npm registry via `bun add`, justified in findings.
- Push: `git push origin feature/mvp` and `git push gitea feature/mvp`.
- Findings and the next kickoff: `docs/findings/STORY-010-findings.md`, `docs/KICKOFF-STORY-011.md`, committed as
  `docs: STORY-010 - findings + KICKOFF-STORY-011`. Set STORY-010's status lines to Done.

## Authority

- Free: `web/` (everything), `README.md` § Web, and in `docs/` only `findings/`, `KICKOFF-*` and STORY-010's status lines.
- Do not touch: `src/pdf_splitter/`, `tests/`, `pyproject.toml`/`uv.lock`, the engine repo, `~/Documents/AI/Inkwell`,
  `~/Documents/Vaults`, `docs/loop-state.json`, PRD/Architecture (report doc errors in findings).
- Out of scope: cut/download/delete/expiry UX (STORY-011 — note that `JobStatus` does not re-poll after a cut is
  queued from the review page, STORY-009 findings § Out-of-Scope); rate limits, `queue_position` (STORY-012);
  Caddy (STORY-013); a thumbnails strip (story § Out of Scope).

## Stopping conditions (BLOCKED protocol)

- An AC can't be met without changing the Architecture or the API (name the section/route).
- `bun install` needs the network and it is unavailable.
- A pre-existing test (web or Python) fails for reasons unrelated to your change.
- You'd need credentials, cloud resources or money.

## Final report shape

Per-AC ✅/❌ with file:line, the counts (`bun run test` before 147 / after N; `uv run pytest` still 335), `bun run check`
and `bun run build` results (bundle size), the manual run's observations (a section selected → both sheets with the
overlay; a cut dragged → the override saved and the plan re-fetched; gutter drag → `column_split` saved; Reset;
keyboard nudge), the commits (on both remotes), and what STORY-011 (cut/download UX) should know: how to trigger the
poll again after `POST /cut`, where the manifest badges come from, and the download endpoints' contracts
(`::test_end_to_end_upload_analyze_plan_cut_download`, `::test_section_pdf_comes_from_the_zip_by_plan_index`,
`::test_delete_marks_the_row_before_removing_the_directory`).
