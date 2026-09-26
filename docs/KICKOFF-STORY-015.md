# KICKOFF — STORY-015: Home page with two entry points + page-range mode

## What you're walking into

"pdf-splitter" is a focused public tool (iLovePDF/Smallpdf style): upload a PDF, work on it, download, auto-delete.
Today it only splits "by chapters". STORY-015 adds the plain **Split by page ranges** next to it, per ADR-009: a
new Plan *source*, not a new pipeline.

- **Web + API (you write code here):** `~/Documents/Repos/pdf-splitter` (GitHub `BigSpoon33/pdf-splitter` = `origin`,
  Gitea mirror = `gitea`), branch **`feature/mvp`**. Stay on it. STORY-011 landed as
  `68769d3 feat: STORY-011 - split, download, delete-now and expiry UX`, its docs commit `5754c1d`, and the gate r1
  fix `fix: STORY-011 - gate r1: server decides expiry, results never outlive a failed refresh, one split per click`
  (`docs/findings/STORY-011-findings.md` § Gate r1 fixes — read it: it changed `Expiry`, `Review`, `Download` and
  added `seconds_left` to the status). Anything after those is the orchestrator's gate work — check
  `git log --oneline -8` and `docs/findings/STORY-011-review.md`.
  **Baselines:** `cd web && bun run test` → **238 pass** (17 files), `bun run check` 0 errors 0 warnings (330 files),
  `bun run build` ≈ 102 kB JS (36.6 kB gzip); `uv run pytest -q` → **345 pass** (≈ 80 s), `uv run ruff check` clean.
  `docs/loop-state.json` belongs to the orchestrator: never stage it.
- **Engine (read-only):** `~/Documents/Repos/monograph-splitter` pinned at `v0.4.1` (STORY-016 bumps it to v0.4.2
  later). The ranges cut must NOT use it — PyMuPDF only (ADR-009).
- Read, in order: `docs/stories/STORY-015.md` (ACs authoritative), `docs/Architecture.md` ADR-009 (§ "Page-range
  mode is a Plan source"), § Job states, § API Interface, ADR-007 (anonymous jobs, 24 h, capability URLs — the id is
  the only credential: never in logs, storage or `document.title`), `docs/PRD.md` AC-14 (the verification you must
  pass end-to-end), then `docs/findings/STORY-011-findings.md`.

Toolchain: SPA is **Bun only** (`bun install` / `bun run dev|check|test|build`, `bunx`; never npm/npx/yarn/pnpm).
Python via `uv` (`uv run pytest`, `uv run ruff check`; `uv add` only if unavoidable — it should not be).
Port 8000 is taken on this laptop: API on 8010, Vite with `API_PORT=8010 bun run dev --port 5181 --strictPort`.

## What STORY-011 established (reuse, don't re-invent)

- **Job page** `web/src/components/JobPage.svelte`: owns the job-level state — `job` (latest status) + `receivedAt`
  (`performance.now()` when it arrived), `gone` (`GoneReason`, the single "deleted screen" signal; `goneWith()`
  first-reason-wins — fed ONLY by API 404/410s, never by a clock), `resume` (bumped to restart the status poll
  after a terminal state, also when the expiry countdown runs out), `jobState` (last of `review`/`done`), `cuts` (cuts seen finishing),
  `reviewable` (sticky: the editor is never unmounted mid-edit, through a cut and a failed cut). `onstatus()`
  detects the `done` that ends a cut (`cutPending`). Props `load`, `pollMs`, `review`, `expiry` are the test seam
  (`JobPage.test.ts` `fakeApi()` follows the API's transitions).
- **Poll** `JobStatus.svelte`: new props `resume` (a counter the effect reads) and `ongone(err)`.
- **Split/results** `Download.svelte` (mounted INSIDE `Review.svelte`, which owns the `PlanEditor`): props
  `id, editor, job, results, resultsError?, retrying?, stale, cut?, oncut?, onretry?, ongone?`. `split()` commits a
  draft, `editor.flush()`, refuses to cut an unsaved/refused plan, `postCut`, sets `editor.edited = false`,
  `oncut()`; `requestedOn` keeps the button disabled from the click until the poll reports the cut (gate r1 F3 —
  keep that if you add a ranges Split), a 409 `busy` to our own POST is followed, not shown. Progress "Cutting
  section N of M…" from `job.progress/total`; results = per-row `<a href={sectionUrl(id, row.index)} download>` +
  size + flags (`badgeFlags`/`flagLabel`) + "Download all (ZIP)" (`resultUrl`); `resultsError` renders an inline
  alert + Retry in place of any list. Ranges manifests have `flags: []` — nothing to change there.
- **Review** `Review.svelte`: props `job, cuts, cut, oncut, onsaved, ongone, retryMs?`; `results` state (separate
  from `editor.rows`); `loadResults()` on each `cuts` bump EMPTIES `results` first, then fetches the manifest — a
  non-gone failure → `resultsError` + one automatic retry (`MANIFEST_RETRY_MS`), never the old rows (gate r1 F2);
  effect on `editor.saves` calls `onsaved()` when the job was `done` (AC-2 of 011: one poll shows `review`); effect
  on `editor.gone` → `ongone`. A ranges review must go through the same `loadResults` path.
- **Expiry / deleted screen** `Expiry.svelte` (props `secondsLeft` = the status's `seconds_left`, `receivedAt`;
  "Files deleted in 23 h" counts down on `performance.now()` — never `Date` vs `expires_at`, gate r1 F1; when the
  count runs out `onexpired` makes the page poll once and only a 404/410 is the deleted screen; Delete now with
  injectable `confirmDelete`/`remove`/`now`/`graceMs`), `Expired.svelte` (`GoneReason` exported from its module
  script), `lib/expiry.ts` (`timeLeft(ms)`, `formatBytes`). Any new status fixture needs `seconds_left`
  (`tests/test_api_e2e.py::test_seconds_left_counts_down_on_the_servers_clock` is the contract).
- **Client** `web/src/lib/api.ts`: `postCut`, `deleteJob`, `resultUrl`, `sectionUrl` (+ STORY-009/010's `getJob`,
  `createJob`, `getAnalysis`, `getPlan`, `putPlan`, `getManifest`, `Plan`/`Section`/`Source` types).
  `Source = 'outline' | 'headings' | 'manual'` — you add `'ranges'` and `Section.endPage?`.
- **Routes** `web/src/lib/route.ts`: `parseRoute(pathname)` (`home`, `job`, `privacy`, `terms`, `not_found`),
  `jobPath(id)`, `navigate(pathname)`, `onNavigate`, `linkClick(e, path)`. **`parseRoute` and `navigate` only see
  `location.pathname`** — `?mode=ranges` (AC-1) needs `location.search` threaded through (`navigate` compares
  pathnames; `onNavigate` passes the pathname) or a plan `source` read from `GET /plan` instead. ADR-009 says the
  SPA routes to `/j/<id>?mode=ranges`; the plan's `source: "ranges"` is the more robust truth after a reload. Decide
  and record it.
- **Upload** `DropZone.svelte` (`oncreated(id)`, injectable `upload`); `App.svelte` mounts it on `home`.

## Backend pointers (ADR-009; stay inside these)

- `src/pdf_splitter/models.py`: `Source = Literal["outline","headings","manual"]` (:26), `Section` (:60, `page`
  bounded by the `pages` validation context), `Plan` (:124, overrides validated against section heights),
  `PreviewRequest` (:161), `validate_plan` (:178). Add `"ranges"`, `Section.endPage: int | None` (≥ page, ≤ pages),
  required in ranges mode, overrides → 422 in ranges mode; pick forbid-or-ignore for `endPage` elsewhere and test it.
  `Plan.dump()` must carry `endPage` only where it applies (the chapter flow's `plan.json` must stay byte-identical).
- Analysis for a ranges job: the upload always analyzes (`worker/task.py:74` `run_analyze` → `default_plan`,
  `worker/analyze.py:165`). A ranges job can reuse that (pages, sizes) and simply PUT a `ranges` plan; do not fork
  the upload path (story note).
- Cut: `worker/task.py:87` `run_cut` → `worker/cut.py:70` `cut_book` (engine). Branch on `plan["source"] ==
  "ranges"` to a PyMuPDF path (`insert_pdf(src, from_page=page-1, to_page=endPage-1)`, sandboxed like the rest —
  it already runs in the worker subprocess), then reuse `zip_entry` (:54) for names and `write_zip` (:93) for the
  atomic ZIP + `manifest.json`; rows need the same keys the manifest route serves (`index, name, file, flags, notes,
  leaks, bytes` — see `ManifestRow` and `::test_manifest_serves_the_last_cuts_rows_from_the_zip`). Progress per
  section through the same `progress(done, total, MSG_CUTTING)` callback. `_reset_outputs` (:62) semantics apply.
- The preview route (`routes/preview.py`) and the source picker/layout panel are chapter-only: a ranges plan should
  never reach them from the SPA; decide whether the API refuses a section-plan preview for a `ranges` plan (422) and
  test it if you do.

## Contracts (the tests ARE the contract — never hand-write sample JSON)

- Plan PUT validation/normalization: `tests/test_api_e2e.py::test_put_plan_rejects_bad_plans_with_field_errors`
  (:238, parametrized `body`→`expected_locs`), `::test_put_plan_normalizes_names_and_returns_the_saved_plan` (:274),
  `::test_put_plan_from_done_returns_the_job_to_review` (:326), `::test_put_plan_and_cut_refused_outside_review_and_done` (:316).
- Cut/download loop: `::test_end_to_end_upload_analyze_plan_cut_download` (:69), `::test_cut_refuses_an_empty_plan`
  (:354), `tests/test_cut.py::test_cut_book_writes_every_section_and_translates_overrides` (:79),
  `::test_write_zip_removes_the_tmp_when_the_rename_fails` (:120), `::test_runner_runs_the_real_cut_task_to_done` (:214).
- Fixtures: `tests/fixtures/books.py::text_book(path, pages)` (:99) writes `pages` sheets whose text says
  `page {n} line …` (0-based n) — exactly what AC-3's "30-page fixture, `1-10, 15-20, 5-7` → 10/6/3 pages with the
  right page text" needs. Put the new tests in `tests/test_ranges.py`.
- SPA: `web/src/lib/api.test.ts` `stubFetch`; component tests inject loaders (`Review.test.ts` `mount()`,
  `JobPage.test.ts` `fakeApi()`); fixtures `web/src/lib/fixtures.ts`.

## Critical gotchas

1. **Chapter flow byte-identical.** Every existing pytest and vitest must stay green unchanged; `plan.json` for
   outline/headings/manual must not grow an `endPage: null` key.
2. **`endPage` is inclusive and 1-based** (ADR-003 sheets). PyMuPDF `insert_pdf` takes 0-based `from_page`/`to_page`,
   both inclusive.
3. **Engine names:** keep the `NNN-<slug>` / `NNN - <display name>.pdf` rule (`engine_name`, `zip_entry`) — overlapping
   ranges may share a display name; `dedupe_names` already suffixes duplicates.
4. **Range parser** (`web/src/lib/ranges.ts`): per-TOKEN errors (malformed, reversed `10-5`, out of range, `0`),
   good tokens still parse — same spirit as `plan.ts:parseList` (per-line errors). "Every N pages" fills the text
   field. Default names `Pages 1–10` (en dash), single page `Page 40`.
5. **Ranges review UI:** no SourcePicker, PagePreview or LayoutPanel (AC-4). `Review.svelte` mounts all three today;
   branch on `editor.plan.source === 'ranges'` (or a `mode` prop) and mount `RangeEditor` + the list + `Download`.
   `PlanEditor.replaceSections(source, sections, label)` already swaps the list with Undo; `setPage` has no
   `endPage` sibling yet. `SectionList` shows a page input per row — for ranges it needs `page`–`endPage` or reuse
   only rename/delete (AC-4 says "where sensible").
6. **Don't remount `Review`** on a cut (its cleanup `editor.destroy()` sends pending saves); `JobPage` already keeps it.
7. **Ports/processes:** `PDFSPLIT_JOBS_DIR=<scratch> uv run pdf-splitter api --port 8010` + `uv run pdf-splitter worker`
   (same env), Vite on 5181. Stop by PID (`lsof -ti :8010`, `kill <pid>`; the worker's PID from
   `pgrep -a -f "pdf-splitter worker"` — read-only), never `pkill -f`. Headless browser: Playwright via
   `uv run --with playwright python <script>` with `executable_path='/usr/bin/chromium'` (the bundled browser is not
   downloaded); scripts live in your scratch dir, not the repo.
8. **`bun run check` must stay at 0 warnings**; visitor-facing API errors go through `errors.ts`
   (`errors.test.ts` counts the API's codes — a new code needs an entry in both tables).

## Recommended AC ordering

1. AC-2 backend: `models.py` (`Source`, `Section.endPage`, ranges validation) + `tests/test_ranges.py` plan
   validation via `PUT /plan` (422 locs for missing/invalid `endPage`, overrides in ranges mode; `endPage` in other
   sources forbidden-or-ignored).
2. AC-3 backend: `worker/cut.py` ranges path (`cut_ranges(job_dir, plan, progress)` next to `cut_book`, dispatched
   in `worker/task.py:run_cut`) + `test_ranges.py` real-PDF cut on `text_book(…, 30)`.
3. AC-4 SPA: `lib/ranges.ts` (`parseRanges(text, pages)`, `everyN(n, pages)`, `formatRanges`) + `ranges.test.ts`;
   `api.ts` types; `RangeEditor.svelte`; `Review.svelte` branch.
4. AC-1 SPA: `App.svelte` home with two entry points (`Home.svelte`), mode through the upload redirect and reload
   (`route.ts` + `route.test.ts`; after analysis a ranges job PUTs its first ranges plan — or starts empty and the
   Split button stays disabled until a valid range exists).
5. AC-5: the STORY-011 `Download` unchanged; headless run of PRD AC-14 (30-page PDF: `1-10, 15-20, 5-7` → 3 PDFs of
   10/6/3 pages; "every 10 pages" → 3 PDFs) plus a chapter-mode smoke run (Maciocia copy in scratch:
   `~/Documents/Vaults/TCM_Knowledge_Base/Books/Maciocia, …libgen.lc.pdf`, never write next to the original).
6. AC-6: `uv run pytest -q && uv run ruff check`; `cd web && bun run check && bun run test && bun run build`.

## Conventions

- Python: FastAPI/Pydantic style of `models.py`/`routes/`; TypeScript strict, Svelte 5 runes/snippets, plain CSS on
  the `app.css` tokens. Comments say WHY, never WHAT.
- Commit on `feature/mvp`: `feat: STORY-015 - home page with chapter and page-range entry points; page-range split mode`,
  ending with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Stage explicit paths only
  (never `git add -A`/`.`; never `docs/loop-state.json`). Never `reset --hard` / `checkout .`.
- Push: `git push origin feature/mvp` and `git push gitea feature/mvp`.
- Findings + next kickoff: `docs/findings/STORY-015-findings.md`, `docs/KICKOFF-STORY-<next>.md` (ask the loop
  state / story list for the next number — likely STORY-012 or STORY-016), committed as
  `docs: STORY-015 - findings + KICKOFF-STORY-<next>`; set STORY-015's status lines to Done.

## Authority

- Free: `web/`, `src/pdf_splitter/models.py`, `src/pdf_splitter/worker/cut.py` + `task.py` (the dispatch),
  `tests/test_ranges.py` (new) and new fixtures, `README.md`, and `docs/Architecture.md` ONLY where the build differs
  from ADR-009 (the story lists it), `docs/findings/`, `KICKOFF-*`, STORY-015's status lines.
- Do not touch: the engine repo, `~/Documents/AI/Inkwell`, `~/Documents/Vaults` (read the Maciocia PDF only by
  copying it), `docs/loop-state.json`, the PRD, existing tests' assertions (add, don't rewrite).
- Out of scope: merge/compress/convert or other hub tools; rate limits / `queue_position` (STORY-012); Caddy
  (STORY-013); terms/privacy text (STORY-014); the engine bump (STORY-016).

## Stopping conditions (BLOCKED protocol)

- An AC can't be met without changing the Architecture beyond ADR-009 (name the section).
- A pre-existing test (web or Python) fails for reasons unrelated to your change.
- `bun install` / `uv sync` needs the network and it is unavailable.
- You'd need credentials, cloud resources or money.

## Final report shape

Per-AC ✅/❌ with file:line; counts (`bun run test` before 238 / after N; `uv run pytest` before 345 / after N);
`bun run check` and `bun run build` (bundle size); the manual run's observations (home page → "Split by page
ranges" → upload → range editor → `1-10, 15-20, 5-7` → Split → 3 PDFs of 10/6/3 pages; every-N; reload keeps the
mode; chapter mode unchanged); the commits (on both remotes); decisions taken (mode in URL vs plan source,
`endPage` forbidden vs ignored, preview refusal); what the next story should know.
