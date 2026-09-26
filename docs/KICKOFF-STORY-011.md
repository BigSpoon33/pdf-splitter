# KICKOFF — STORY-011: SPA: cut, download, delete and expiry UX

## What you're walking into

"pdf-splitter" is a public site: someone drops in a big PDF and gets back one PDF per chapter or section.
The product lives in two repos:

- **Web (you write code here):** `~/Documents/Repos/pdf-splitter` (GitHub `BigSpoon33/pdf-splitter` = `origin`,
  Gitea mirror = `gitea`), branch **`feature/mvp`**. Stay on that branch. STORY-010 landed as
  `e0a10cd feat: STORY-010 - page preview with hatched cuts and draggable cut, gutter and band lines`, its docs
  commit `7fd52c2`, then the gate r1 fix `fix: STORY-010 - gate r1: preview plans the list on screen, per-sheet
  geometry, focus on drag, errors scoped to the selection` (the preview route now takes the client's section list —
  `docs/findings/STORY-010-findings.md` § Gate r1 fixes). Anything after those is the orchestrator's gate work; check
  `git log --oneline -8`. **Baselines:** `cd web && bun run test` → **190 pass** (13 files, ≈ 3.5 s), `bun run check`
  0 errors 0 warnings (320 files), `bun run build` ≈ 92 kB JS (33.3 kB gzip); `uv run pytest` → **344 pass** (≈ 80 s),
  `uv run ruff check` clean. `docs/loop-state.json` belongs to the orchestrator: never stage it. Line numbers below
  are as of `e0a10cd`; search by symbol if they drift.
- **Engine (read-only):** `~/Documents/Repos/monograph-splitter`, pinned at `v0.4.1`. You should not need it.
- Read, in order: `docs/stories/STORY-011.md` (its ACs are authoritative), `docs/Architecture.md` § web (SPA), § API
  Interface, § Job states, ADR-007 (anonymous jobs, 24 h retention, capability URLs — the id is the only credential:
  never in logs, storage or `document.title`), then `docs/findings/STORY-010-findings.md` (§ Handoff, § Out-of-Scope),
  `docs/findings/STORY-009-findings.md` (§ Gate r2 fix — how leaving the page is handled; § Out-of-Scope — the
  `JobStatus` re-poll gap) and `docs/findings/STORY-007-findings.md` AC-4/AC-5 (the cut and download routes).

Toolchain: the SPA is **Bun only** (`bun install` / `bun run dev|check|test|build`, `bunx`; never npm/npx/yarn/pnpm).
`uv` only to run the API. Port 8000 is taken on this laptop: run the API on 8010 and Vite with `API_PORT=8010 bun run dev`.

## What STORY-009/010 established (use these, don't re-invent)

- **The page:** `web/src/App.svelte` — two routes (`web/src/lib/route.ts`: `/` and `/j/<id>`; `navigate()`,
  `onNavigate()`, `jobPath()`), a `<header class="site">`, `<main>` with `DropZone` (home) or `JobPage` (job, keyed by
  id) + a "Split another PDF" link, and a `not_found` card. **There is no footer** (AC-5 adds one) and no `/privacy` /
  `/terms` route: `parseRoute` (`route.ts:11`) returns `not_found` for them today — add the routes there (static
  pages; STORY-014 writes the text) and test `route.test.ts`.
- **`JobPage.svelte`** mounts `JobStatus` (polls `GET /api/jobs/{id}` every `POLL_MS` 1.5 s while `queued`/`running`,
  stops on `review`/`done`/`failed` — `TERMINAL_STATES`, `api.ts:61` — reports every status through `onstatus`, shows
  `fatal` on 404/410 (`isGone`) and a `hiccup` line on other failures) and, from `review`/`done` on, `Review` with
  `jobState`. **Gap you must close (AC-1):** after `POST /cut` the job goes `queued(cut) → running(cut) → done`, but
  `JobStatus`'s effect has already returned on the terminal state, so nothing polls. Two options: (a) remount
  `JobStatus` (`{#key}` on a counter the cut button bumps — simplest, but `Review` must NOT be remounted with it or the
  editor's `destroy()` runs mid-edit); (b) give `JobStatus` a `resume` signal/prop (a `$state` counter read by the
  effect) so the same instance polls again. Either way the results list needs the `done` status to fetch the manifest.
  `Review` gets its rows from `getManifest` at mount (`Review.svelte:55`) — after a NEW cut the rows must be refreshed
  (the editor's `rows` is public: `editor.rows = await getManifest(...)`), and `hasManifest`/`stale` logic there
  (`Review.svelte:90`) already shows "cut again to refresh the files" when the plan moved on.
- **The plan store** `web/src/lib/editor.svelte.ts` `PlanEditor`: `plan`, `selected`, `picker`, `rows` (manifest rows;
  `SectionList` badges from them via `badgesFor`, `plan.ts:244`), `dirty`/`edited`/`saving`/`error`/`gone`,
  `saves` (accepted saves, `:87`), edit methods incl. `setOverride` (:233), `setSetting`, `flush()`, `destroy()`.
  **AC-2** ("editing after a cut returns to review"): the API already does it server-side (a PUT from `done` returns
  the row to `review`, Architecture § Job states) and `Review` shows the stale note while `edited`; what is missing is
  the UI state: the results list must stay (downloads remain valid until the next cut — the manifest route keeps
  serving the last ZIP's rows) but the status must read `review`. Key it off `editor.saves`/`editor.edited` or poll
  once after a save — decide and record it in findings.
- **The preview** `web/src/components/PagePreview.svelte` — nothing to do here; it asks the engine again on
  `editor.saves` (only when the body it would send differs from the last one: it sends the LOCAL settings, override
  AND section list — `SectionPlanRequest.sections`, gate r1), so a re-cut changes nothing in it. It sets
  `editor.gone`/`editor.error` on a 410 `expired` / 404 `not_found` from a preview; a 422 (`no_section`) is its own
  inline message. Every sheet is drawn in its own `analysis.size[n-1]`.
- **Types and client** `web/src/lib/api.ts`: `JobStatus` (:11, `expires_at` ISO string — AC-3's "files deleted in
  23 h" comes from it), `getJob`, `getManifest` (:258, `ManifestRow` :218: `index, name, file, flags, notes, leaks,
  bytes`), `isGone`, `ApiError.userMessage` (`errors.ts` table: `not_ready`, `busy`, `expired`, `not_found`,
  `no_section`… — `errors.test.ts` counts the API's codes, 14 today),
  `request()`/`fetchOk()` (:107/:95). **Add:** `postCut(id)` (202 `{id, state:'queued'}`), `deleteJob(id)` (204),
  `resultUrl(id)` / `sectionUrl(id, i)` — downloads are plain `<a href download>` to the API (an attachment response;
  same-origin through the Vite proxy / Caddy), NOT fetched into blobs (a 56 MB ZIP is not for memory). `api.test.ts`
  stubs `fetch` (`stubFetch`).
- **CSS:** tokens on `:root` in `web/src/app.css`, global `.card .error .bar .badge .toast .field .field-error`,
  `button.primary` (unused so far — the Split button is the one primary action). Svelte 5 runes/snippets only.
- **Tests:** vitest + @testing-library/svelte (jsdom). Fixtures `web/src/lib/fixtures.ts` (`analysisOf`, `planOf`,
  `rowOf`, `bigPlanOf`). `JobStatus.test.ts` shows the polling harness (fake timers + `settle()`, never `vi.waitFor`
  with fake timers); `Review.test.ts` `mount()` injects every loader (incl. `loadSectionPlan`/`loadSheet` and a
  stubbed `URL.createObjectURL`); `App`-level routing is in `route.test.ts`. A `confirm()` for "Delete now" must be
  injectable or stubbed (`vi.stubGlobal('confirm', …)`); a `<a download>` click in jsdom does nothing — assert the
  `href`.

## API contracts you'll use (the tests ARE the contract — never hand-write sample JSON)

- `POST /api/jobs/{id}/cut` → 202 `{id, state:"queued"}`; the row becomes `queued`/`kind: cut` with progress reset —
  `tests/test_api_e2e.py::test_cut_queues_once_and_resets_the_row` (:339; a second POST while queued/running → 409
  `busy`), `::test_cut_refuses_an_empty_plan` (:354, 422), `::test_cut_from_done_recuts` (:363). Progress while
  running: `GET /api/jobs/{id}` `{progress, total, message: "Cutting sections"}` (per section) — the same status the
  poll already renders (`JobStatus.svelte` `label()`: "Waiting to cut" / "Cutting sections"). `queue_position` is
  `null` until STORY-012.
- `GET /api/jobs/{id}/result.zip` → `application/zip`, `Content-Disposition: attachment; filename*=utf-8''<filename>-sections.zip`;
  `GET /api/jobs/{id}/sections/{i}.pdf` → `application/pdf`, attachment, `Content-Length` = the row's `bytes`, `i` =
  the PLAN index (`manifest[].index`); 404 for an index not in the ZIP; 409 `not_ready` before any cut —
  `::test_end_to_end_upload_analyze_plan_cut_download` (:69, the whole loop incl. the ZIP's entry names
  `NNN - <name>.pdf` + `manifest.json`), `::test_section_pdf_comes_from_the_zip_by_plan_index` (:668, unicode names,
  the 404/422 cases), `::test_downloads_before_a_cut_are_not_ready` (:662).
- `GET /api/jobs/{id}/manifest` → the last cut's rows (409 before any cut; kept until the next cut replaces the ZIP) —
  `::test_manifest_serves_the_last_cuts_rows_from_the_zip` (:698). Flags per row are what `SectionList` badges
  (`badgeFlags`/`flagLabel`, `plan.ts`); the results list can reuse `flagLabel`.
- `DELETE /api/jobs/{id}` → 204, the row is marked `deleted` BEFORE the directory goes, everything answers 410
  `expired` afterwards — `::test_delete_marks_the_row_before_removing_the_directory` (:688),
  `::test_unknown_deleted_and_expired_jobs` (:157: 404 `not_found` for an unknown id, 410 for deleted AND for
  `expires_at` in the past). `expires_at` is on every status (`::test_job_status_shape_hides_the_requeue_marker_and_shows_failures`, :134).
- A preview or a save answering 410 mid-page already flips `editor.gone` (STORY-009/010): AC-4's deleted screen should
  key off ONE signal (`JobStatus`'s `fatal`, `editor.gone`, or a page-level `gone` state the others feed) so the
  whole `/j/<id>` page — not just a card — becomes the deleted screen with the "Split another PDF" link.

## Critical gotchas

1. **Ports.** `PDFSPLIT_JOBS_DIR=<scratch> uv run pdf-splitter api --port 8010` + `uv run pdf-splitter worker` (same
   env), then `cd web && API_PORT=8010 bun run dev`. Stop what you start by PID (`lsof -ti :8010`, `kill <pid>`),
   never `pkill -f`. A quick book: `PYTHONPATH=tests uv run python -c "from fixtures.books import headed_book;
   headed_book('b.pdf')"`; the real one is the Maciocia copy in a scratch dir (never write next to the original).
2. **Downloads through the dev proxy**: Vite proxies `/api` to 8010 (`web/vite.config.ts`), so `<a href="/api/jobs/…/result.zip">`
   works in dev and behind Caddy alike. Do not `fetch()` the ZIP.
3. **`confirm()` and `download` in jsdom**: inject or stub; assert hrefs, not navigation.
4. **Leaving the page** is handled by the editor (gate r2): "Delete now" should `editor.destroy()`-proof itself —
   a delete while a save is pending gets a 410 for the save, which is fine (`gone` set, nothing retried); just make
   sure the deleted screen wins over the editor's error line.
5. **The expiry clock**: render `expires_at` relative to now ("files deleted in 23 h"), refresh it at most once a
   minute; a past `expires_at` = the deleted screen (the API says 410 anyway).
6. **Do not remount `Review` on a cut**: its cleanup calls `editor.destroy()` (sends a pending save). Remount or
   re-signal `JobStatus` only.
7. **`bun run check` must stay at 0 warnings** (a11y: a "Delete now" `<button>` with a confirm is fine; a clickable
   `<div>` is not). Every visitor-facing string goes through `errors.ts` where an API code is involved.
8. **Stay out of the Python side.** Cut/download/delete routes are done and tested; the janitor deletes expired dirs.
   If an AC needs an API change (e.g. a `Retry-After`), findings + BLOCKED protocol.

## Recommended ordering

1. `api.ts`: `postCut`, `deleteJob`, `resultUrl`, `sectionUrl` (+ `api.test.ts` with `stubFetch`).
2. `route.ts`: `/privacy`, `/terms` (+ `route.test.ts`); `App.svelte` footer + two static page components (AC-5).
3. `JobStatus.svelte`: a way to poll again after a cut (option a or b above) + `JobStatus.test.ts`.
4. `Download.svelte` (AC-1/AC-2): the Split button (disabled while `editor.dirty`/`saving`, or flush first), progress
   while `queued`/`running` cut (from the status), the results list from `getManifest` after `done` (per-row link +
   flags via `flagLabel`, "Download all (ZIP)"), kept when the job goes back to `review` (AC-2). Mount in `JobPage`.
5. `Expired.svelte` (AC-3/AC-4): the expiry line ("files deleted in 23 h"), "Delete now" with confirm → `deleteJob` →
   the deleted screen; the same screen for a 410 at load or mid-session; "Split another PDF" link.
6. `cd web && bun run check && bun run test && bun run build`, then the manual run (API on 8010 + worker; headed_book,
   then Maciocia for a real 23-section cut ≈ 15 s; a headless-browser screenshot of the results list and the
   deleted screen).

## Conventions

- TypeScript strict, Svelte 5 runes, small components, plain CSS on the tokens. Comments say WHY, never WHAT.
  `bun run check` clean (0 warnings).
- Commit on `feature/mvp`: `feat: STORY-011 - split, download, delete-now and expiry UX`, ending with
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Stage explicit paths only (never
  `git add -A` / `.`; never `docs/loop-state.json`). Never `reset --hard` / `checkout .`.
- New dependencies: avoid; if one is unavoidable, from the official npm registry via `bun add`, justified in findings.
- Push: `git push origin feature/mvp` and `git push gitea feature/mvp`.
- Findings and the next kickoff: `docs/findings/STORY-011-findings.md`, `docs/KICKOFF-STORY-012.md`, committed as
  `docs: STORY-011 - findings + KICKOFF-STORY-012`. Set STORY-011's status lines to Done.

## Authority

- Free: `web/` (everything), `README.md` § Web, and in `docs/` only `findings/`, `KICKOFF-*` and STORY-011's status lines.
- Do not touch: `src/pdf_splitter/`, `tests/`, `pyproject.toml`/`uv.lock`, the engine repo, `~/Documents/AI/Inkwell`,
  `~/Documents/Vaults`, `docs/loop-state.json`, PRD/Architecture (report doc errors in findings).
- Out of scope: terms/privacy TEXT (STORY-014 — the pages exist with a placeholder); rate limits and `queue_position`
  (STORY-012); Caddy (STORY-013); anything in the preview.

## Stopping conditions (BLOCKED protocol)

- An AC can't be met without changing the Architecture or the API (name the section/route).
- `bun install` needs the network and it is unavailable.
- A pre-existing test (web or Python) fails for reasons unrelated to your change.
- You'd need credentials, cloud resources or money.

## Final report shape

Per-AC ✅/❌ with file:line, the counts (`bun run test` before 183 / after N; `uv run pytest` still 335), `bun run check`
and `bun run build` results (bundle size), the manual run's observations (Split → progress → results list with
links + flags → a section PDF and the ZIP downloaded through the proxy; an edit after the cut → status back to review,
links still there; Delete now → deleted screen; a stale `/j/<id>` → deleted screen; footer links), the commits (on
both remotes), and what STORY-012 (rate limits, `queue_position`) should know: where the status card renders
`queue_position` (`JobStatus.svelte`, already conditional on non-null), and which routes answer 429 `rate_limited`
today (`tests/test_upload.py`).
