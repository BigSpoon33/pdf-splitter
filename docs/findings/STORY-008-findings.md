# Findings — STORY-008
**Date:** 2026-09-25
**Status:** done

Commits: `a9044de feat: STORY-008 - SPA scaffold with drop zone and live job status`, gate r1 fix
`c3bc2e5 fix: STORY-008 - gate r1: first-poll errors are shown; the live region settles on a fatal error` (feature/mvp).

## AC Verification
- [x] AC-1: `web/` is Svelte 5 + Vite 8 + TypeScript (strict) — `web/package.json:6-12` (scripts `dev`/`build`/`check`/`test`),
  `web/vite.config.ts:10-16` (the `/api` proxy targets `http://localhost:${API_PORT}`, default **8000**; `changeOrigin`, no
  rewrite), `web/tsconfig.json`. `bun run build` emits `web/dist/` (static); `bun run check` is clean (0 errors, 0 warnings).
- [x] AC-2: `web/src/components/DropZone.svelte` — drag/drop on the zone (`onDrop`, :61), click-to-pick through a
  visually-hidden `<input type=file accept=".pdf,application/pdf">` inside the `<label>` (:69-103, keyboard-focusable),
  `precheck` (:5, `.pdf` name OR `application/pdf` type, empty file → `not_pdf`, `> maxBytes` → `too_large`, same messages
  as the API codes), upload % from XHR progress (:87-98, `createJob` in `web/src/lib/api.ts:111`).
- [x] AC-3: after the upload `App.svelte:26` pushes `/j/<id>`; `JobStatus.svelte:20-45` polls `getJob` every `POLL_MS`
  (1500, `web/src/lib/config.ts:7`) while `queued`/`running`, stops on `review`/`done`/`failed` and on 404/410
  (`isGone`), keeps retrying a transient failure (network/5xx) with a "Retrying…" note. It shows the state (per kind:
  "Waiting to analyze", "Analyzing", "Cutting sections", …), `message`, a `progress / total` bar and count, and
  "Position in queue: n" only when `queue_position` is non-null (:76). Reloading `/j/<id>` resumes: the route is parsed
  from `location.pathname` (`web/src/lib/route.ts:11`), no client state.
- [x] AC-4: `web/src/lib/errors.ts:6` — one `MESSAGES` table: the 13 codes of `src/pdf_splitter/errors.py:MESSAGES`,
  `rate_limited` + `disk_full` (Architecture § API Interface, STORY-012), and the client-only `network`;
  `messageFor(code)` falls back to `FALLBACK_MESSAGE` for anything else (incl. `__proto__`/`toString`).
- [x] AC-5: vitest + @testing-library/svelte (jsdom). `web/src/components/DropZone.test.ts` (precheck table, click
  rejection, drop rejection, click upload with % and the id handed over, drop upload, the API's `not_pdf` rendered in
  place, unknown code fallback) and `web/src/lib/errors.test.ts` (reads `src/pdf_splitter/errors.py` and
  `docs/Architecture.md` via Vite `?raw` imports and asserts every code there has a message, so the table cannot drift
  and nothing is hand-copied). Extra: `api.test.ts`, `JobStatus.test.ts`, `route.test.ts`.

## Test Results
**Command:** `cd web && bun run check && bun run test && bun run build`
**Result:** pass
```
svelte-check: COMPLETED 302 FILES 0 ERRORS 0 WARNINGS 0 FILES_WITH_PROBLEMS
vitest:       Test Files  5 passed (5) · Tests  64 passed (64)       (before: 0 — there was no web/)
vite build:   dist/index.html 0.53 kB · dist/assets/index-*.css 2.70 kB (gzip 1.11) · dist/assets/index-*.js 43.74 kB (gzip 17.28)
```
**Command:** `uv run pytest` → `332 passed in 64.01s` (unchanged) · `uv run ruff check` → `All checks passed!`

**Manual run** (API `--port 8010` + worker on a scratch `PDFSPLIT_JOBS_DIR`, `API_PORT=8010 bun run dev --port 5178`,
headless Chromium via playwright-core from a scratch dir — not a repo dependency). DOM text, 1500-page `text_book`:
```
0.0s /       | Uploading… 0%
0.1s /       | Uploading… 100%
0.2s /j/<id> | big.pdf · 1500 pages  Waiting to analyze
1.7s /j/<id> | big.pdf · 1500 pages  Analyzing  Indexing pages  977 / 1500
3.2s /j/<id> | big.pdf · 1500 pages  Analyzing  Finding headings  1500 / 1500
4.7s /j/<id> | big.pdf · 1500 pages  Ready for review  The analysis is finished. …
```
- Reload mid-analysis: before `Analyzing Indexing pages 764 / 1500`, after reload the same, then on to review; poll gaps
  1506 ms / (reload) / 1506 ms, **0 polls after `review`**.
- Reload at review: same URL, `Ready for review`. `document.title` stays `PDF Splitter` (never the id).
- A PNG dropped as `x.pdf` (`application/pdf`): the API answered 400, rendered in place: "This file is not a PDF.", URL
  stays `/`. `cover.png` picked: refused by the precheck, no request. `/j/AAAAAAAAAAAAAAAAAAAAAA`: "There is no job at
  this address. Check the link." (404, no further polls). Console: only Vite's HMR lines and the browser's own
  "Failed to load resource" for the 400/404 responses — the app logs nothing.

## Decisions (small ambiguities resolved)
- **Dependencies** (all devDependencies, official npm registry via `bun add -d`): `svelte` 5.57 (UI), `vite` 8.3
  (dev server/build), `@sveltejs/vite-plugin-svelte` 7.3 (Vite's Svelte compiler — the story's list omits it but Vite
  cannot build `.svelte` without it), `typescript` **6.0** (7.0 installed first but svelte-check 4.7's peer range is
  `^5 || ^6`), `svelte-check` 4.7, `vitest` 5.0, `@testing-library/svelte` 5.4 (its `svelteTesting()` Vite plugin
  sets the browser resolve conditions and auto-cleanup), `jsdom` 30 (vitest's DOM). No runtime dependencies; no router
  or state library. `bun.lock` is the only lockfile.
- **Proxy port:** `API_PORT` from the environment or `web/.env.local` (read with `loadEnv(mode, '.', ['API_'])`, so no
  `process`/`@types/node`), default 8000 as the story says. `web/.env.local` is gitignored.
- **errors.test.ts reads files outside `web/`:** Vite denies `?raw` imports outside the root, so `vite.config.ts:19`
  widens `server.fs.allow` to `../src/pdf_splitter` and `../docs` **only when `mode === 'test'`**; the dev server never
  serves them (jobs/ is never reachable).
- **Messages are the SPA's own wording** of each code (the visitor sees the table, never the raw server text);
  `expired` is Architecture § web's exact "This job was deleted (files are kept 24 h)." `ApiError.message` keeps the
  server's text for debugging; UI code uses `ApiError.userMessage`.
- **Non-JSON error bodies** (a proxy's HTML page): 413 → `too_large` (Caddy's body cap, STORY-013), anything else →
  `internal`. A fetch/XHR network failure → client code `network`.
- **Precheck:** a file passes when its name ends `.pdf` (any case) OR its type is `application/pdf` (some OSes send no
  type); a 0-byte file is `not_pdf` (the API does the same). Only the first dropped file is used.
- **Transient poll failures** (network, 5xx) keep polling at 1.5 s with a "<message> Retrying…" note — also before the
  first successful poll (gate r1); only 404/410 are final. `aria-busy` is true exactly while the loop is still polling.
- **Privacy (ADR-007):** `<meta name="referrer" content="no-referrer">` so `/j/<id>` never leaks through a Referer; the
  title is fixed; nothing is logged or stored. An inline `data:,` favicon avoids a stray `/favicon.ico` request.
- **Routing without a library:** `route.ts` (`parseRoute`, `jobPath`, `navigate`, `onNavigate`); unknown paths render a
  "Page not found" card. `/j/<id>` accepts `[A-Za-z0-9_-]{1,64}` (ids are `token_urlsafe(16)`, 22 chars).
- **Production SPA fallback:** whatever serves `web/dist/` must answer `index.html` for `/j/*` (Vite's dev server does
  this already) — STORY-013's Caddyfile needs `try_files {path} /index.html`. Noted in README § Web.

## Gate r1 fixes (`c3bc2e5`)
Review: `docs/findings/STORY-008-review.md` (2 confirmed, 1 refuted).
- **First-poll errors were swallowed** (`JobStatus.svelte`, the `!job` branch): a network/5xx answer before the first
  successful poll showed a bare "Loading…" forever. Now that branch renders `<message> Retrying…` (`role=status`) and
  polling continues; the first success replaces it with the job UI. Test: `JobStatus.test.ts` "shows a transient error
  before the first answer, then the job once a poll succeeds" (network → 500 → running).
- **`aria-busy` stuck at true after 404/410**: it followed `active` (job non-terminal) and ignored `fatal`. It now
  follows a `polling` derived (`!fatal && (job === null || non-terminal)`), the loop's own stop conditions. Tests:
  "clears aria-busy once polling stops on a fatal error" (running → 410 → `aria-busy="false"` + the alert) and "clears
  aria-busy on a terminal state". Both new tests fail against `a9044de`'s component and pass now.
- **Copy decision (orchestrator):** `no_text_layer` now says plainly that OCR isn't supported — "This PDF has no text
  layer (it looks scanned). OCR isn't supported yet — run OCR on it first, then upload it again." — in `web/src/lib/errors.ts`
  and, as directed, in `src/pdf_splitter/errors.py:MESSAGES` (no Python test pins the text; pytest 332 still pass).
  Pinned in `errors.test.ts` "says plainly that OCR is not supported (PRD AC-8)".
- After: `bun run check` 0/0, `bun run test` **68 passed** (5 files), `bun run build` 44.12 kB JS (gzip 17.39),
  `uv run pytest` 332 passed, `uv run ruff check` clean.

## Bugs Found
none in the API (it behaved exactly as its tests pin it). Two SPA bugs were found by gate r1 and fixed — see above.

## Handoff Context for Next Session
The API client is `web/src/lib/api.ts` (only `getJob`/`createJob` so far; add `getAnalysis`/`getPlan`/`putPlan`
through the same private `request()` so every non-2xx is an `ApiError` with the 422 `errors[]`), and `/j/<id>`
renders `JobStatus`; STORY-009 mounts the review UI there once `state` is `review`/`done`. The manifest's per-section
`flags` (STORY-009 AC-3) have no JSON endpoint: they exist only inside `result.zip` — see Out-of-Scope.

## Out-of-Scope Items
- **API gap for STORY-009 AC-3 ("flags from the last cut shown as badges"):** the cut's `manifest.json` (per-section
  `flags`, `leaks`) is only served inside `result.zip`; there is no `GET /api/jobs/{id}/manifest`. STORY-009 either needs
  a small API route (an Architecture § API Interface change, i.e. a decision) or shows the per-section preview's
  `flags` from `POST /sections/{i}/plan` instead (one sandboxed engine run per section — too slow for a whole list).
- **Doc gaps (not changed here):** Architecture § API Interface's POST line still omits `unreadable` (STORY-005 noted it);
  § web says the dev proxy targets `:8000` — true by default, override with `API_PORT`.
- `queue_position` stays null until STORY-012; `rate_limited`/`disk_full` are in the table ahead of STORY-012.
- Caddy `try_files` for `/j/*` (STORY-013).
