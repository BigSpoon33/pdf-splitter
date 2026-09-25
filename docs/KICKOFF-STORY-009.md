# KICKOFF — STORY-009: SPA: source picker, section list, layout panel

## What you're walking into

"pdf-splitter" is a public site: someone drops in a big PDF and gets back one PDF per chapter or section.
The product lives in two repos:

- **Web (you write code here):** `~/Documents/Repos/pdf-splitter` (GitHub `BigSpoon33/pdf-splitter` = `origin`,
  Gitea mirror = `gitea`), branch **`feature/mvp`**. Stay on that branch. STORY-008 landed as
  `a9044de feat: STORY-008 - SPA scaffold with drop zone and live job status` and its docs commit
  `docs: STORY-008 - findings + KICKOFF-STORY-009`. Anything after those is the orchestrator's gate work; check
  `git log --oneline -8`. **Baselines:** `cd web && bun run test` → **64 pass** (5 files, ≈ 1.5 s), `bun run check` 0 errors
  0 warnings, `bun run build` ≈ 44 kB JS (17 kB gzip); `uv run pytest` → **332 pass** (≈ 65 s), `uv run ruff check` clean.
  `docs/loop-state.json` belongs to the orchestrator: never stage it.
- **Engine (read-only, you won't need it):** `~/Documents/Repos/monograph-splitter`, pinned at `v0.4.1`.
- Read, in order: `docs/stories/STORY-009.md` (its ACs are authoritative), `docs/Architecture.md` § web (SPA), § Data
  Types (Analysis, Plan), § API Interface, ADR-003 (sheet numbers, not printed pages), then
  `docs/findings/STORY-008-findings.md` (§ Decisions, § Out-of-Scope — the manifest gap) and
  `docs/findings/STORY-007-findings.md` § Decisions / § Handoff.

Toolchain: the SPA is **Bun only** (`bun install` / `bun run dev|check|test|build`, `bunx` for one-offs; never
npm/npx/yarn/pnpm). `uv` only to run the API. Port 8000 is taken on this laptop by an unrelated process: run the API
on 8010 and start Vite with `API_PORT=8010 bun run dev` (see gotcha 1).

## What STORY-008 established (use these, don't re-invent)

- **`web/`**: Svelte **5** (runes: `$state`, `$derived`, `$effect`, `$props`), Vite 8, TypeScript 6 strict
  (`noUncheckedIndexedAccess`), plain CSS. Tokens on `:root` + `prefers-color-scheme: dark` in `web/src/app.css`
  (`--bg --surface --fg --muted --border --accent --accent-soft --danger --danger-soft --ok --radius --focus`) and
  global `.card`, `.error`, `.bar > span` (progress bar), `.visually-hidden`. No runtime dependencies.
- **`web/src/lib/api.ts`** — the typed client. Contract tests: `web/src/lib/api.test.ts`.
  - `JobStatus` / `JobState` / `JobKind` / `JobErrorCode` — the `GET /api/jobs/{id}` shape
    (`tests/test_api_e2e.py::test_job_status_shape_hides_the_requeue_marker_and_shows_failures`).
  - `ApiError {status, code, message (server text), errors?: FieldError[], requestId?, userMessage}`;
    `FieldError {loc, msg, type}` (the 422 body, `::test_put_plan_rejects_bad_plans_with_field_errors`).
    `errorFromBody(status, text)` turns any error body into one (`api.test.ts › errorFromBody keeps field errors`).
  - private `request<T>(url, init)` — fetch wrapper: JSON `Accept`, network failure → `ApiError(0, 'network')`,
    non-2xx → `errorFromBody`. **Add `getAnalysis`, `getPlan`, `putPlan` on top of it** (don't hand-roll fetch).
  - `getJob(id, signal?)`, `createJob(file, onProgress?, makeXhr?)` (XHR, for upload progress), `isGone(err)`
    (404/410 → stop), `TERMINAL_STATES`, and `jobUrl(id, suffix)` (private; escapes the id).
- **`web/src/lib/errors.ts`** — `MESSAGES` (every API code + `rate_limited`, `disk_full`, client `network`),
  `messageFor(code)`, `FALLBACK_MESSAGE`. `web/src/lib/errors.test.ts` reads `src/pdf_splitter/errors.py` and
  Architecture and fails if a code has no message — if an API change adds a code, the test tells you.
- **Routing** — `web/src/lib/route.ts`: `parseRoute(pathname)` → `home | job{id} | not_found`, `jobPath(id)`,
  `navigate(path)` (pushState + notify), `onNavigate(listener)`. `web/src/App.svelte` renders `DropZone` on `/` and
  `{#key route.id}<JobStatus id=…/>{/key}` on `/j/<id>`. The id lives only in the URL (ADR-007): never log it, never put
  it in `document.title` or storage; `index.html` sets `referrer: no-referrer`.
- **`web/src/components/JobStatus.svelte`** — props `{id, load = getJob, pollMs = POLL_MS}`; polls while
  queued/running, stops on review/done/failed/404/410. It owns the `JobStatus` value internally; STORY-009 needs it
  outward (see ordering step 1). Injecting the loader as a prop is how its tests avoid the network
  (`JobStatus.test.ts`), and the same pattern suits the new components.
- **`web/src/components/DropZone.svelte`** — `precheck(file, maxBytes)` exported from its `<script module>`.
- **Tests** — vitest (`jsdom`, `bun run test` = `vitest run`), @testing-library/svelte (`render`, `screen`,
  `fireEvent`). With fake timers, don't use `vi.waitFor` for timing assertions: it advances the fake clock itself
  (see `settle()` in `JobStatus.test.ts`).
- **API endpoints you'll call first** (all already built by STORY-007; the tests are the contract):
  - `GET /api/jobs/{id}/analysis` — Analysis keys `::test_analysis_and_plan_exist_from_review_on`; full shape
    `tests/test_worker.py::test_analyze_outline_book` (outline `levels` = item counts per level, `items[{name, page,
    heading, level, y?}]`, `headings {body_size, levels[{size,count}], candidates[{name, page, heading, size, level, y,
    col}]}`, `size[{W,H}]`, `pageLabels[]` (`""` when the PDF has none), `suggested {source, level}`) and
    `::test_analyze_headings_book_without_outline` (no outline → `{"levels": [], "items": []}`), `::test_suggest`.
    409 `not_ready` before `review`.
  - `GET /api/jobs/{id}/plan` — the saved Plan (`tests/test_worker.py::test_default_plan_from_outline_and_headings`,
    `::test_default_plan_settings_round_trip_and_match_the_index_profile`).
  - `PUT /api/jobs/{id}/plan` — 200 returns the **normalized** Plan (names trimmed, duplicates suffixed ` (2)`, only the
    override keys sent): `::test_put_plan_normalizes_names_and_returns_the_saved_plan`,
    `::test_put_plan_deduplicates_long_names_within_the_cap`. 422 `invalid` + `errors[].loc` like
    `["body","sections",0,"page"]` / `["body","settings","column_split"]` / `["body","overrides"]`:
    `::test_put_plan_rejects_bad_plans_with_field_errors` (the full loc table — AC-5 maps these to controls),
    `::test_put_plan_rejects_non_finite_numbers`. 409 `busy` while a job runs, from `done` the job returns to `review`:
    `::test_put_plan_and_cut_refused_outside_review_and_done`, `::test_put_plan_from_done_returns_the_job_to_review`.
    Setting ranges live in `src/pdf_splitter/models.py:PlanSettings` (`column_split` 0.2–0.8, bands 0–200 pt,
    `heading_min_size` 4–72); `Section` (`name` 1–120 chars, `page` ≥ 1 and ≤ pages).
  - Later (STORY-010): `GET /sheets/{n}.png?dpi=48|72|110`, `POST /sections/{i}/plan`
    (`::test_section_plan_returns_the_engine_view_with_rects`).

## Critical gotchas

1. **Ports.** Run the API with `PDFSPLIT_JOBS_DIR=<scratch> uv run pdf-splitter api --port 8010` plus
   `uv run pdf-splitter worker` (same env), then `cd web && API_PORT=8010 bun run dev`. Never hard-code 8010. Stop what you
   start by PID (`lsof -ti :8010`, `kill <pid>`), never `pkill -f`.
2. **Flags from the last cut (AC-3) have no JSON endpoint.** The manifest (per-section `flags`, `leaks`) exists only
   inside `result.zip` (`::test_end_to_end_upload_analyze_plan_cut_download` reads it from the zip). Options: (a) a new
   `GET /api/jobs/{id}/manifest` route — an API + Architecture § API Interface change, outside `web/`, so it's a decision
   for the orchestrator (BLOCKED protocol, or record it and ship the badge UI against a typed stub); (b) unzip client-side
   (a zip dependency — no). Don't call `POST /sections/{i}/plan` per section for badges: one sandboxed engine run each.
   Also: the last section always carries `span-clamped` (STORY-007 findings § Out-of-Scope) — don't badge it as a warning.
3. **Sheet numbers, not printed pages (ADR-003).** `page` is the 1-based sheet; `analysis.pageLabels[page-1]` is the
   printed label, shown only as a hint when non-empty.
4. **Debounced PUT (AC-2, 600 ms)** must not race: keep one request in flight, send the latest plan after it, and use
   the 200 body as the new truth (the server normalizes names). A 409 `busy` means a cut is running — show it, don't
   retry-loop. 410/404 → the job is gone (`isGone`).
5. **Headings filtering is client-side** (story note): `analysis.headings.candidates` carry `size` and `level`; the
   threshold slider is in multiples of `body_size`. `settings.heading_min_size` is the engine's detection floor (a
   LayoutPanel input), not the same thing as the slider — keep them apart in `plan.ts`.
6. **Overrides are keyed by section INDEX** (`"0"`, `"1"`, …). Delete/merge/insert shifts indexes: re-key or drop
   overrides accordingly, or the API rejects keys past the end (`::test_put_plan_rejects_bad_plans_with_field_errors`,
   the `overrides={"1": …}` case).
7. **Svelte 5 only** (no `export let`, no `$:`, no stores unless needed; `.svelte.ts` for rune-based modules like a plan
   store). `bun run check` must stay at 0 warnings — a11y warnings count.
8. **Tests must not need the network or the API**: inject loaders as props or `vi.stubGlobal('fetch', …)` as
   `api.test.ts` does.
9. **Stay out of the Python side.** `src/`, `tests/`, `pyproject.toml`, `uv.lock` are done; a needed API change goes in
   findings (gotcha 2).

## Recommended ordering

1. Lift the job: make `JobStatus` report its latest status (an `onstatus` callback prop or a small `job.svelte.ts`
   store) so `App.svelte`'s `/j/<id>` view can mount the review UI when `state ∈ {review, done}` — the poll already
   stops there.
2. `web/src/lib/api.ts`: `Analysis`, `Plan`, `PlanSettings`, `Section`, `Override` types (from the tests above and
   `src/pdf_splitter/models.py`), `getAnalysis(id)`, `getPlan(id)`, `putPlan(id, plan)`; extend `api.test.ts`.
3. `web/src/lib/plan.ts` (pure functions, most of the tests): outline sections at level n, heading sections at
   threshold/level, paste-list parser (`Name, page` per line → sections + per-line errors), merge-with-next, override
   re-keying; `plan.test.ts` (AC-1, AC-3, AC-6).
4. `SourcePicker.svelte` (AC-1) + source switch with undo toast + debounced `putPlan` (AC-2).
5. `SectionList.svelte` (AC-3), `LayoutPanel.svelte` (AC-4), 422 `errors[].loc` → the offending control (AC-5).
6. `cd web && bun run check && bun run test && bun run build`, then the manual run (API on 8010 + worker, a
   `headed_book` from `tests/fixtures/books.py` — `PYTHONPATH=tests uv run python -c "from fixtures.books import
   headed_book; headed_book('b.pdf')"`): switch sources, edit, reload `/j/<id>` and see the saved plan come back.

## Conventions

- TypeScript strict, Svelte 5 runes, small components, plain CSS using the tokens. Comments explain WHY, never WHAT.
  `bun run check` clean.
- Commit on `feature/mvp`: `feat: STORY-009 - source picker, editable section list and layout panel`, ending with
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Stage explicit paths only (never
  `git add -A` / `.` from the root; never `docs/loop-state.json`). Never `reset --hard` / `checkout .`.
- New dependencies: avoid; if one is unavoidable, from the official npm registry via `bun add`, and justify it in findings.
- Push: `git push origin feature/mvp` and `git push gitea feature/mvp`.
- Findings and the next kickoff: `docs/findings/STORY-009-findings.md`, `docs/KICKOFF-STORY-010.md`, committed as
  `docs: STORY-009 - findings + KICKOFF-STORY-010`. Set STORY-009's status lines to Done.

## Authority

- Free: `web/` (everything), `README.md` § Web, and in `docs/` only `findings/`, `KICKOFF-*` and STORY-009's status lines.
- Do not touch: `src/pdf_splitter/`, `tests/`, `pyproject.toml`/`uv.lock`, the engine repo, `~/Documents/AI/Inkwell`,
  `~/Documents/Vaults`, `docs/loop-state.json`, PRD/Architecture (report doc errors in findings).
- Out of scope: PagePreview, overlays, draggable cuts, gutter drag (STORY-010); cut/download/delete/expiry UX
  (STORY-011); rate limits, `queue_position` (STORY-012); Caddy (STORY-013).

## Stopping conditions (BLOCKED protocol)

- An AC can't be met without changing the Architecture or the API (name the section/route) — gotcha 2 is the known one.
- `bun install` needs the network and it is unavailable.
- A pre-existing test (web or Python) fails for reasons unrelated to your change.
- You'd need credentials, cloud resources or money.

## Final report shape

Per-AC ✅/❌ with file:line, the counts (`bun run test` before 64 / after N; `uv run pytest` still 332), `bun run check`
and `bun run build` results (bundle size), the manual run's observations (source switch + undo, an edit persisted and
reloaded, a 422 rendered next to its control), how the manifest-flags question was resolved, the commits (on both
remotes), and what STORY-010 (PagePreview) should know: the plan store's API, where the selected section lives, and the
preview endpoints' contracts (`::test_section_plan_returns_the_engine_view_with_rects`,
`::test_sheet_png_renders_through_the_sandboxed_subprocess_and_caches`, the 410-during-render tests).
