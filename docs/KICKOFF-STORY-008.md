# KICKOFF — STORY-008: SPA: scaffold, drop zone, job status

## What you're walking into

"pdf-splitter" is a public site: someone drops in a big PDF and gets back one PDF per chapter or section.
The product lives in two repos:

- **Web (you write code here):** `~/Documents/Repos/pdf-splitter` (GitHub `BigSpoon33/pdf-splitter` = `origin`,
  Gitea mirror = `gitea`), branch **`feature/mvp`**. Stay on that branch. STORY-007 landed as
  `6fcf848 feat: STORY-007 - plan/preview/cut/download API and the cut task` plus its docs commit
  (`docs: STORY-007 - findings + KICKOFF-STORY-008`). Anything after those is the orchestrator's gate work. Check
  `git log --oneline -8`. **Baseline: 326 tests pass (`uv run pytest`, ≈ 80 s: real sandboxed subprocesses and
  deliberate timeouts), `uv run ruff check` is clean.** There is NO `web/` directory yet and NO Bun project: you
  create it. `docs/loop-state.json` belongs to the orchestrator, so never stage it.
- **Engine (read-only, you won't need it):** `~/Documents/Repos/monograph-splitter`, pinned at `v0.4.1`.
- Read, in order: `docs/stories/STORY-008.md` (its ACs are authoritative), `docs/Architecture.md` § web (SPA),
  § API Interface, § Job states, ADR-006 (Svelte + Vite + TS, built with Bun, served by Caddy), ADR-007
  (capability URLs), then `docs/findings/STORY-007-findings.md` (§ Decisions and § Handoff: the codes, the
  polling contract) and `docs/findings/STORY-005-findings.md` § Decisions (the upload's error body).

Toolchain: Python side `uv 0.12.10` (only to run the API); the SPA is **Bun only** (never npm/yarn/pnpm/npx;
`bunx` for one-offs). `bun --version` first. Port 8000 is taken on this laptop by an unrelated process: run the
API on 8010+ and point the Vite proxy there (see gotcha 1).

## What STORY-007 established (use these, don't re-invent)

The backend contract is complete. Every shape below is pinned by a test; read the test, not this summary, when
you write the client.

- **Error bodies**, every route: `{code, message}`; 422s add `errors: [{loc: [...], msg, type}]`; the 500 adds
  `request_id`. The single code table is `src/pdf_splitter/errors.py:MESSAGES` (13 codes: `too_large`, `not_pdf`,
  `encrypted`, `too_many_pages`, `no_text_layer`, `unreadable`, `not_found`, `expired`, `not_ready`, `busy`,
  `invalid`, `preview_failed`, `internal`). Architecture § api also names `rate_limited` (429) and `disk_full`
  (503), which STORY-012 will add: put them in `errors.ts` now (AC-4 says every code in Architecture). Contracts:
  `tests/test_api_e2e.py::assert_error` (the body shape), `tests/test_upload.py::assert_rejected` (the upload's).
- **Every response carries `X-Request-ID`**; send one if you like (`[A-Za-z0-9-]{8,64}` is kept, anything else
  replaced). Contract: `::test_every_response_carries_a_request_id`, `::test_unexpected_errors_return_500_json_with_a_request_id`.
- **`POST /api/jobs`** multipart `file` → 201 `{id, state: "queued"}` (`src/pdf_splitter/upload.py:create_job`).
  Rejections: 400 `not_pdf|encrypted|no_text_layer|unreadable`, 413 `too_large|too_many_pages`. The limits the
  DropZone prechecks are `PDFSPLIT_MAX_BYTES` (200 MiB) and `.pdf`; the API checks `%PDF-` itself. Contracts:
  `tests/test_upload.py::test_upload_creates_queued_analyze_job`, `::test_too_large`, `::test_not_pdf_png_renamed`,
  `::test_encrypted`, `::test_too_many_pages`, `::test_no_text_layer_image_only`, `::test_unreadable_garbage_after_magic`.
- **`GET /api/jobs/{id}`** (`src/pdf_splitter/routes/plan.py:status_of`) →
  `{id, state, kind, progress, total, queue_position, message, error_code, expires_at, filename, pages}`.
  `state ∈ queued|running|review|done|failed` (`deleted` and expired rows are 410 `expired`; unknown 404
  `not_found`). `kind ∈ analyze|cut` says what is queued/running. `progress/total` are page counts during
  analyze and section counts during a cut; `message` is the phase (`Indexing pages`, `Reading the outline`,
  `Finding headings`, `Preparing the book`, `Cutting sections`, `Packaging the sections`) or the failure text
  when `failed`. `error_code` is non-null ONLY when `state == "failed"` (`timeout|resources|internal`).
  `queue_position` is `null` until STORY-012: render nothing for it, not "0". **Polling contract:** poll every
  1.5 s while `state ∈ {queued, running}`; stop on `review`/`done`/`failed`, on 410, and on 404. Contracts:
  `tests/test_api_e2e.py::test_job_status_shape_hides_the_requeue_marker_and_shows_failures`,
  `::test_unknown_deleted_and_expired_jobs`, `::test_end_to_end_upload_analyze_plan_cut_download` (the states a
  job passes through).
- Not needed by STORY-008 but already there for STORY-009+: `GET /analysis` and `GET/PUT /plan` (409 `not_ready`
  before `review`; PUT 422 `invalid` with field errors, 409 `busy` while a job runs, from `done` the job returns
  to `review`), `GET /sheets/{n}.png?dpi=48|72|110`, `POST /sections/{i}/plan` (the Section plan with `rects` on
  1-based sheets), `POST /cut` (202; 409 `busy`/`not_ready`; 422 for an empty plan), `GET /result.zip` and
  `/sections/{i}.pdf` (attachments; 409 `not_ready` before the first cut), `DELETE` (204). Contracts: the rest
  of `tests/test_api_e2e.py` (test names are listed per AC in `docs/findings/STORY-007-findings.md`).
- **Running the stack for real:** `PDFSPLIT_JOBS_DIR=<scratch> uv run pdf-splitter api --port 8010` and, in
  another shell with the same env, `uv run pdf-splitter worker`. A 6-page synthetic book analyzes in ≈ 1.5 s;
  a 1300-page book in ≈ 25 s and cuts in ≈ 13 s. `tests/fixtures/books.py::headed_book(path)` makes a real
  test PDF (`uv run python -c "from fixtures.books import headed_book; headed_book('b.pdf')"` with
  `PYTHONPATH=tests`). Stop servers by PID (`lsof -ti :8010`, then `kill <pid>`), never `pkill -f`.

## Critical gotchas

1. **Ports.** The story says the dev server proxies `/api` to `:8000`; that port is held by an unrelated process
   on this laptop. Make the proxy target configurable (`VITE_API_URL` or an env-read `API_PORT` in
   `vite.config.ts`, default 8000) and run the API on 8010 for your manual check. Don't hard-code 8010.
2. **Bun only.** `bun create vite` / `bun install` / `bun run dev|build|check|test`; `bunx` for `svelte-check`
   if it isn't a script. No `package-lock.json`/`yarn.lock` may appear; `bun.lock` (text) is the lockfile.
   Keep deps to what the story lists: svelte, vite, typescript, svelte-check, vitest, @testing-library/svelte
   (+ jsdom for vitest's DOM). Check the installed Svelte major (5: runes) and write for it.
3. **The job id is the credential** (ADR-007). It lives in the URL (`/j/<id>`) and nowhere else: never log it
   to the console, never put it in `document.title`, never send it to a third party. Reload must resume from the
   URL alone (AC-3), so there is no client state to persist.
4. **Upload progress needs XHR** (`fetch` has no upload progress); the rest of the client can be `fetch`. On
   an XHR error read the JSON body for `{code}`; a network failure or a non-JSON body maps to a generic message.
5. **Every error the API returns has a `code`**; `errors.ts` is a `Record<code, message>` plus a fallback for an
   unknown code (the table above + `rate_limited` + `disk_full`). AC-5 tests it against the full list: generate
   the expected set from `src/pdf_splitter/errors.py` in the test's comment or fixture, don't hand-copy twice.
6. **`error_code` vs `code`.** The job row's `error_code` (`timeout|resources|internal`) is a different namespace
   from the response `code`; JobStatus shows the row's `message` for a failed job (already user-facing) and
   only needs `error_code` for an icon, if at all.
7. **410 means "gone"**: "This job was deleted (files are kept 24 h)" (Architecture § web). 404 means the URL is
   wrong. Don't poll after either.
8. **Vite's proxy and the multipart upload:** `changeOrigin: true` and no `rewrite`; the API paths already start
   with `/api`. The API's CORS is not configured (same-origin through the proxy in dev, Caddy in prod), so don't
   call `http://localhost:8010` directly from the browser.
9. **Tests must not need the network or the API**: mock `fetch`/XHR in vitest. `bun run test` must be
   `vitest run` (not watch mode) or CI hangs.
10. **Stay out of the Python side.** STORY-008 is `web/` only; if the API needs a change (it shouldn't), record
    it in findings rather than editing `src/`. `docs/loop-state.json` is the orchestrator's.

## Recommended ordering

1. AC-1: `web/` scaffold (`bun create vite web --template svelte-ts` or by hand), `vite.config.ts` proxy
   (`/api` → `http://localhost:${API_PORT}`), scripts `dev`/`build`/`check` (`svelte-check --tsconfig
   ./tsconfig.json`)/`test` (`vitest run`). Plain CSS with tokens on `:root` + `prefers-color-scheme` dark.
   Verify `bun run check` and `bun run build` are clean before writing a component.
2. AC-4: `src/lib/errors.ts` (the code → message table + `messageFor(code)`), then `src/lib/api.ts`: typed
   `JobStatus` (the `status_of` shape), `ApiError` (`{status, code, message, errors?, request_id?}`),
   `createJob(file, onProgress)` (XHR), `getJob(id)` (fetch), with every non-2xx turned into `ApiError`.
3. AC-2: `DropZone.svelte` (drag/drop + click-to-pick, `.pdf`/`application/pdf` and `MAX_BYTES` precheck with
   the same messages as `too_large`/`not_pdf`, an upload % bar).
4. AC-3: routing without a router lib (`/` and `/j/<id>` from `location.pathname` + `history.pushState`),
   `JobStatus.svelte` polling every 1.5 s while queued/running, showing state, `message`, `progress/total` and
   the queue position when it is not null; a reload of `/j/<id>` resumes. On `review`/`done`/`failed`, stop.
5. AC-5: vitest + @testing-library/svelte for the DropZone precheck (type, size, click and drop) and the
   `errors.ts` table (every code has a message; unknown code falls back).
6. `cd web && bun run check && bun run test && bun run build`, then the manual run: API on 8010 + worker, `bun
   run dev`, drop `b.pdf`, watch `/j/<id>` go queued → running (progress) → review, reload it, and try a PNG
   as `x.pdf` (400 `not_pdf` rendered in place). Screenshot or copy the DOM text into findings.

## Conventions

- TypeScript strict, Svelte 5 idioms if that is what installs, small components, plain CSS. Comments explain
  WHY, never WHAT. `bun run check` must be clean.
- Commit on `feature/mvp`: `feat: STORY-008 - SPA scaffold with drop zone and live job status`, ending with
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Stage explicit paths only (never
  `git add -A` / `.`; never `docs/loop-state.json`; never `web/node_modules`, add it to `.gitignore` together with
  `web/dist`). Never `reset --hard` / `checkout .`.
- Push: `git push origin feature/mvp` and `git push gitea feature/mvp`.
- Findings and the next kickoff go in `docs/findings/STORY-008-findings.md` and `docs/KICKOFF-STORY-009.md`,
  committed as `docs: STORY-008 - findings + KICKOFF-STORY-009`. Set STORY-008's status lines to Done.

## Authority

- Free: `web/` (everything), `README.md` (a "Web" section: how to run the SPA against the API), `.gitignore`,
  and in `docs/` only `findings/`, `KICKOFF-*` and STORY-008's status lines.
- Do not touch: `src/pdf_splitter/`, `tests/`, `pyproject.toml`/`uv.lock` (the API is done; a needed change
  goes in findings), the engine repo, `~/Documents/AI/Inkwell`, `~/Documents/Vaults`, `docs/loop-state.json`,
  PRD/Architecture (report doc errors in findings).
- Out of scope: the review UI (SourcePicker, SectionList, LayoutPanel, PagePreview, Download: STORY-009+),
  Caddy/compose (STORY-013), rate limits and the disk guard (STORY-012).

## Stopping conditions (BLOCKED protocol)

- An AC can't be met without changing the Architecture or the API (name the section/route).
- `bun` is missing or `bun install` needs the network and it is unavailable.
- A pre-existing test fails for reasons unrelated to your change.
- You'd need credentials, cloud resources or money.

## Final report shape

Per-AC ✅/❌ with file:line, the counts (`bun run test` before 0 / after N; `uv run pytest` still 326), the
`bun run check` and `bun run build` results (bundle size), the manual run's observed states (drop → `/j/<id>`
→ queued → running `n/total` → review, a reload, a rejected upload's message), the commits (on both remotes),
and what STORY-009 (the review UI) should know: the `api.ts` surface (function names and the types, cited as
the vitest tests that pin them), how routing is done, and which endpoints of STORY-007 it will call first
(`GET /analysis`, `GET/PUT /plan`, `GET /sheets/{n}.png`, `POST /sections/{i}/plan`), citing
`tests/test_api_e2e.py` for their shapes.
