# KICKOFF — STORY-012: Limits: rate limiting, disk guard, janitor, queue position

## What you're walking into

"pdf-splitter" is a focused public tool (iLovePDF/Smallpdf style): upload a PDF, split it by chapters or by page
ranges, download, auto-delete. STORY-007..011 built the job API, the worker, the review editor and the cut/download
UX; STORY-015 added page-range mode (ADR-009). Nothing yet stops one visitor from filling the disk or starving the
queue, and nothing deletes anything: the `rate` table exists but is never written, `expires_at` is only *read*
(`gone()` answers 410 past it, `Store.expired()` lists rows), `queue_position` is always `null`, and the health
route's `disk_free_gb` is informational. STORY-012 makes those real, plus three orchestrator addenda at the bottom of
`docs/stories/STORY-012.md` (read them — they are ACs in all but name).

- **Web + API (you write code here):** `~/Documents/Repos/pdf-splitter` (GitHub `BigSpoon33/pdf-splitter` = `origin`,
  Gitea mirror = `gitea`), branch **`feature/mvp`**. Stay on it. STORY-015 landed as
  `722b231 feat: STORY-015 - home page with chapter and page-range entry points; page-range split mode` and its docs
  commit `docs: STORY-015 - findings + KICKOFF-STORY-012`. Anything after those is the orchestrator's gate work —
  check `git log --oneline -8` and `docs/findings/STORY-015-review.md` if it exists (gate fixes may have moved lines).
  **Baselines:** `uv run pytest -q` → **367 pass** (≈ 80 s), `uv run ruff check` clean; `cd web && bun run test` →
  **274 pass** (20 files), `bun run check` 0 errors 0 warnings (336 files), `bun run build` ≈ 109 kB JS (38.9 kB gzip).
  `docs/loop-state.json` belongs to the orchestrator: never stage it.
- **Engine (read-only):** `~/Documents/Repos/monograph-splitter` pinned at `v0.4.1` (STORY-016 bumps it). Not touched
  by this story.
- Read, in order: `docs/stories/STORY-012.md` (ACs + the three addenda), `docs/Architecture.md` § Component Map
  "janitor" (:123 — every 5 min, expired dirs + rows, orphan dirs, 503 under 2 GB free), § Job states (:218 — incl.
  the "Failed cuts are recoverable" paragraph, Shuma-approved), § API Interface (:192 — `429 rate_limited`,
  `503 disk_full` on POST, `queue_position` in the status), § Storage Schema (:294 — `rate (ip_hash, at)` "sliding
  window; pruned by the janitor"), ADR-007 (:282 — the IP is stored only as a salted hash, the salt rotates daily),
  `docs/PRD.md` AC-10, AC-11 and Q-3 (:103 — caps 200 MB / 2,000 pages / 6 per IP per hour / 2 workers stand unless
  Shuma changes them; nobody is watching, so the defaults stand), then `docs/findings/STORY-015-findings.md`
  § Handoff and § Out-of-Scope, and `docs/findings/STORY-011-findings.md` § Out-of-Scope (the failed-cut dead end).

Toolchain: SPA is **Bun only** (`bun install` / `bun run dev|check|test|build`, `bunx`; never npm/npx/yarn/pnpm).
Python via `uv` (`uv run pytest`, `uv run ruff check`; `uv add` only if unavoidable — it should not be).
Port 8000 is taken on this laptop: API on 8010, Vite with `API_PORT=8010 bun run dev --port 5181 --strictPort`.

## What exists today (reuse, don't re-invent)

- **Settings** `src/pdf_splitter/config.py:11` `Settings` (`PDFSPLIT_*` env): `jobs_dir`, `max_bytes`, `max_pages`,
  `ttl_hours` 24, `workers` 2, **`rate_per_hour` 6 (already there, unused)**, `analyze_timeout`, `cut_timeout`,
  `public_url`. Add `min_free_gb: float = 2` (AC-2) and whatever the trusted-proxy rule needs (AC-1: e.g.
  `trusted_proxy: str | None`), nothing else. `tests/test_config.py` pins the env mapping — extend it.
- **Store** `src/pdf_splitter/store.py`: schema `:12` (`jobs`, index `jobs_expiry` on `expires_at`, `:32`
  `rate (ip_hash TEXT, at TEXT)`); `:42` `now_ts(now)` (ISO UTC, every method takes an injectable `now`);
  `:101` `create_job(..., ttl_hours, now=)`; `:135` `queue_length()`; `:138` `claim_next(kind, now)`; `:170`
  `set_state`; `:188` `transition(job_id, from_states, to, kind=, error_code=, message=, now=)` (conditional, the
  only way rows change state); `:228` `expired(now)` (strictly `expires_at < now`, oldest first —
  `tests/test_store.py::test_expired_without_sleeping` is the frozen-clock pattern: `make(store, now=T0)`, then
  query with `now=T0 + timedelta(...)`). `tests/test_store.py::test_schema_matches_architecture` compares the schema
  with § Storage Schema — a new column or index goes in both.
- **Upload** `src/pdf_splitter/upload.py`: `:38-44` the per-process `_IP_SALT` + `ip_hash(host)` (the comment says
  STORY-012 replaces it with the daily salt + trusted-proxy rule); `:112` `_accept(file, request, settings, store)` —
  the job dir is created before the copy and removed unless `created`; the row is inserted at `:132` with
  `ip_hash(request.client.host)`. The rate check and the disk guard belong BEFORE `job_dir.mkdir()` (nothing on disk
  for a refused upload); `reject(code)` (`:63`) builds the error body — `Retry-After` is a header it does not set yet.
  Codes `rate_limited` / `disk_full` are NOT in `src/pdf_splitter/errors.py:MESSAGES` yet (the SPA's
  `web/src/lib/errors.ts` already has both, and `web/src/lib/errors.test.ts` counts the API's table against it —
  adding them to the API is required, and keeps that test green).
- **Status** `src/pdf_splitter/routes/plan.py:36` `status_of(job)` returns `"queue_position": None` — the docstring
  says it is STORY-012's. The SPA already renders it: `web/src/components/JobStatus.svelte:94` "Position in queue: N"
  when `queued` and non-null (`JobStatus.test.ts:82`). Contract for the shape:
  `tests/test_api_e2e.py::test_job_status_shape_hides_the_requeue_marker_and_shows_failures` (`STATUS_KEYS`, `:135`).
- **Job routes' gate** `src/pdf_splitter/routes/common.py:16` `EDITABLE = ("review", "done")`, `:17` `BUSY`,
  `:20` `gone(job)` (deleted or past `expires_at` → 410), `:25` `load_job`, `:52` `require_editable`, `:57`
  `saved_plan(settings, job)` = `exists()` then `read_json` — **addendum 2**: a DELETE between the two is a 500
  today; every read of job files after `load_job` must map a vanished file to 410. The preview routes already do it
  (`routes/preview.py:65` `settled()` re-reads the row and raises the 410; `:82` `_cached` reads instead of
  `exists()`; `tests/test_api_e2e.py:529-745` "…after a delete is 410 not 500" tests are the pattern — hook the DELETE
  between the check and the read with `monkeypatch`). `routes/download.py:31` `_result_zip` has the same
  `exists()`-then-open shape.
- **Worker** `src/pdf_splitter/worker/runner.py:67` `Runner(settings, kinds, poll=)`: `:133` `recover(store, now)`
  (re-queue/fail stuck `running` rows; `now` injectable), `:151` `_maybe_sweep` (throttled by `SWEEP_EVERY_S` on
  `time.monotonic()`, called from every loop iteration), `:160` `run_once`, `:169` `_loop` (one `Store` per thread),
  `:185` `serve` (threads = `settings.workers`, `recover` once at start). **AC-3: the janitor lives here** — same
  shape as `_maybe_sweep`: a throttled call from the loop, or its own daemon thread started in `serve`, every 5 min;
  log and continue on any failure (Architecture: idempotent). `tests/test_worker.py:493-630` test `Runner` with a
  real `wstore`; `test_cut.py:150` defines the `wstore` fixture (copy it).
- **Delete** `routes/download.py:76` `delete_job`: row → `deleted` first, then `rmtree` — the janitor's per-row
  order should be the same (addendum 1: a `deleted` row whose dir came back — a late `mkdir(parents=True)` from the
  engine, see `preview.py:65` — gets its dir removed again).
- **Failed cut today** (addendum 3): `routes/common.py:16` `EDITABLE` excludes `failed`, so `PUT /plan` / `POST /cut`
  on `failed`+`cut` answer 409 `not_ready` (`tests/test_api_e2e.py::test_put_plan_and_cut_refused_outside_review_and_done`
  pins `("failed", "analyze", "not_ready")` — keep that row, ADD `failed`+`cut` → 200/202). `analyzed(job)` (`:40`)
  already counts `kind == "cut"` as analyzed. SPA: `web/src/components/Download.svelte:40-44` `failed` disables
  Split and `:103` says "Upload the PDF again to retry"; `Review.svelte` never unmounts on a failed cut
  (`JobPage.svelte` `reviewable` is sticky), so only Download's `failed` gating and message change, plus
  `JobPage.svelte:46` `onstatus` — a `failed` cut currently does not bump `cuts` (correct: the previous ZIP stays).
  `JobPage.test.ts` `fakeApi()` (`:40`) follows the API's transitions; add a `failed` phase to it for the vitest.
- **Health** `src/pdf_splitter/app.py:43`: `disk_free_gb` = `shutil.disk_usage(settings.jobs_dir).free` — the disk
  guard uses the same call (AC-5 says monkeypatch `shutil.disk_usage`; patch it where the guard imports it).
- **Access log / ids** `src/pdf_splitter/access_log.py`, `store.py:51` `log_id`: job ids never appear in logs
  (`tests/helpers.py::assert_id_gone`, `tests/test_upload.py::test_logs_never_contain_a_job_id`) — the janitor's log
  lines use `log_id(...)` too. IPs never appear anywhere (ADR-007): the rate limiter logs the hash prefix at most.
- **Page-range jobs (STORY-015)** need nothing from this story, but the janitor deletes their dirs like any other;
  `tests/test_ranges.py` has a 30-page `ranges_template` fixture and `seed_ranges_job` if you need a bigger job.

## Contracts (the tests ARE the contract — never hand-write sample JSON)

- Upload success/refusals: `tests/test_upload.py::test_upload_creates_queued_analyze_job` (:83), `::test_too_large`
  (:120), `::test_concurrent_uploads_all_succeed` (:361 — the rate limit must not trip it: 6/h per IP, count the
  uploads that test makes, or give each a different peer via the trusted-proxy header); error body shape
  `tests/test_api_e2e.py::assert_error` (`{code, message}` + `x-request-id`).
- Status shape: `::test_job_status_shape_hides_the_requeue_marker_and_shows_failures` (:135) and
  `::test_seconds_left_counts_down_on_the_servers_clock` (:159); the SPA type `web/src/lib/api.ts:JobStatus`.
- Gone jobs: `::test_unknown_deleted_and_expired_jobs` (:176 — 410 on every route past `expires_at`, DELETE
  idempotent); `::test_delete_marks_the_row_before_removing_the_directory` (:765).
- Editable states: `::test_put_plan_and_cut_refused_outside_review_and_done` (:316), `::test_put_plan_from_done_returns_the_job_to_review`
  (:326), `::test_cut_from_done_recuts` (:382).
- Store clock: `tests/test_store.py::test_expired_without_sleeping` (:227), `::test_schema_matches_architecture` (:28).
- Runner: `tests/test_worker.py::test_runner_runs_the_real_analyze_task_to_review` (:493),
  `::test_runner_never_resurrects_a_job_deleted_while_running` (:621); `tests/test_cut.py::test_run_cut_does_not_recreate_a_job_deleted_while_the_engine_ran`.
- Health: `tests/test_health.py::test_health_shape_and_jobs_dir_created` (:17).
- SPA: `web/src/components/JobStatus.test.ts:82` (queue position), `Download.test.ts` (Split gating),
  `JobPage.test.ts` (`fakeApi`, page-level transitions), `web/src/lib/errors.test.ts` (API codes ↔ messages).

## Critical gotchas

1. **Frozen clocks, no sleeping.** Every store method takes `now`; the janitor and the rate window must too
   (`janitor.run(store, settings, now=)`, `ratelimit.check(store, ip_hash, now=)`), so AC-5's tests pass `now` and
   never sleep. `tests/test_worker.py` `FakeClock` (:231) is the pattern for monotonic throttles.
2. **The daily-rotating salt must be shared by every API process/thread** (ADR-007) — a per-process random salt
   (today's `_IP_SALT`) would make the window per-process. Derive it from a secret + the UTC date
   (`PDFSPLIT_IP_SALT` setting, default random once per process is NOT enough for multi-worker uvicorn — say so if
   you keep it single-process). Never store or log the raw IP.
3. **`X-Forwarded-For` only from the trusted proxy** (AC-1): read it only when `request.client.host` equals the
   configured proxy address (Caddy on the same VM, STORY-013); take the LAST untrusted hop, not the first value.
   Without a configured proxy, the peer address is the client.
4. **429 carries `Retry-After`** (seconds until the oldest hit in the window falls out); `reject()` in `upload.py`
   builds bodies with no headers today.
5. **Refused uploads touch nothing on disk**: check rate and disk before `job_dir.mkdir()`; the body is streamed to
   disk only after both pass (a 200 MB upload from a rate-limited client should not be read to the end — FastAPI
   reads the multipart before the handler, so at least never write it).
6. **The janitor must not race the worker**: a row `running` past `expires_at` (a 24 h-old job still cutting cannot
   happen with a 600 s timeout, but a clock skew can) — delete the row first (`set_state deleted`), then the dir,
   the same order as `delete_job`; the task's "row still running?" checks (`task.py:98`, `runner.recover`) then do
   the right thing. Orphan dirs = directories under `jobs_dir` with no row (never `jobs.db*`); `deleted` rows'
   dirs (addendum 1); `rate` rows and `deleted` rows older than 7 days are pruned.
7. **`saved_plan` race → 410 (addendum 2)**: `exists()` then `read_json` — replace with a read that catches
   `FileNotFoundError`, re-reads the row and answers 410 when it is gone (`preview.py:settled` does this). Test it
   with a DELETE hooked between the check and the read (`monkeypatch` `read_json` in `routes.common`).
8. **Recoverable failed cut (addendum 3)**: `EDITABLE` becomes state-AND-kind aware (`failed` only with `kind == "cut"`);
   `store.transition(job_id, EDITABLE, "queued", kind="cut")` in `post_cut` takes a tuple of states — extend the
   conditional so `failed/cut` qualifies but `failed/analyze` never does. `PUT /plan` from `failed/cut` → `review`
   (like from `done`). SPA: Split enabled after a failed cut with the failure message above it; the editor stays.
9. **`queue_position`** = 1 + the number of queued rows of the same kind created before this one (FIFO per kind,
   `claim_next`), `null` unless `queued`. Cheap SQL in `status_of`'s caller (it needs the store) — `get_job`
   (`routes/plan.py:56`) has `StoreDep`.
10. **Bun/uv only; `bun run check` stays at 0 warnings; commit messages end with the Co-Authored-By line; stage
    explicit paths (never `git add -A`/`.`; never `docs/loop-state.json`); never `reset --hard` / `checkout .`.**
    Stop servers by PID (`lsof -ti :8010`, `kill <pid>`; the worker via `pgrep -a -f "pdf-splitter worker"`), never
    `pkill -f`.

## Recommended AC ordering

1. AC-2 disk guard: `settings.min_free_gb`; a `disk_full(settings)` check in `upload.py:_accept` before `mkdir`
   (503 `disk_full` in `errors.py:MESSAGES`); `tests/test_limits.py` with `monkeypatch.setattr(upload.shutil,
   "disk_usage", ...)` (patch where it is looked up).
2. AC-1 rate limit: `src/pdf_splitter/ratelimit.py` (`client_ip(request, trusted_proxy)`, `ip_hash(ip, now)` with
   the daily salt, `check(store, ip_hash, per_hour, now) -> retry_after | None`, `record(...)`); `Store` gets
   `rate_hits(ip_hash, since)` / `add_rate(ip_hash, at)` / `prune_rate(before)`; 429 `rate_limited` + `Retry-After`.
   Move `ip_hash` out of `upload.py` (keep the row's `ip_hash` column fed).
3. AC-4 queue position: `Store.queue_position(job)`; `status_of` takes it; extend the status-shape test and add a
   pytest with three queued jobs; the SPA needs no change (`JobStatus.svelte:94`).
4. AC-3 janitor: `src/pdf_splitter/janitor.py` `sweep(store, settings, now=) -> counts` (expired rows → `deleted` +
   rmtree; `deleted` rows' dirs; orphan dirs; prune `rate` + old `deleted` rows), wired into `Runner` every 5 min
   (`JANITOR_EVERY_S`); logs counts only (no ids beyond `log_id`).
5. Addenda: `saved_plan` race → 410 (+ `_result_zip` if you touch it); recoverable failed cut (API + SPA + tests both
   sides, incl. `failed/analyze` staying 409).
6. AC-5 + verification: `uv run pytest -q && uv run ruff check`; `cd web && bun run check && bun run test && bun run
   build`; a short live run: API :8010 + worker with `PDFSPLIT_RATE_PER_HOUR=2` → the 3rd upload gets 429 with
   `Retry-After`; `PDFSPLIT_TTL_HOURS` can't go below 1 h — move `expires_at` into the past in `jobs.db` (the
   STORY-011 reviewers did this) and watch the janitor delete the dir and the SPA show the deleted screen.
   Update `docs/Architecture.md` (§ Job states, § API Interface, the janitor component) and `README.md`
   (Configuration: the new `PDFSPLIT_*` keys) only where the build differs from what they already say.

## Conventions

- Python: FastAPI/Pydantic style of `models.py`/`routes/`; TypeScript strict, Svelte 5 runes/snippets, plain CSS on
  the `app.css` tokens. Comments say WHY, never WHAT.
- Commit on `feature/mvp`: `feat: STORY-012 - rate limit, disk guard, 24 h janitor and queue position`, ending with
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Stage explicit paths only.
- Push: `git push origin feature/mvp` and `git push gitea feature/mvp`.
- Findings + next kickoff: `docs/findings/STORY-012-findings.md`, `docs/KICKOFF-STORY-016.md` (the loop queue is
  STORY-015 → 012 → 016 → 013; STORY-014 is held), committed as `docs: STORY-012 - findings + KICKOFF-STORY-016`;
  set STORY-012's status lines to Done.

## Authority

- Free: `src/pdf_splitter/{ratelimit,janitor}.py` (new), `config.py`, `store.py`, `upload.py`, `errors.py`,
  `routes/common.py`, `routes/plan.py` (`status_of`, `put_plan`, `post_cut`), `routes/download.py` (only the
  `exists()`-then-read shape), `worker/runner.py`, `app.py` (health), `tests/test_limits.py` (new) + additions to
  existing tests, `web/src/components/Download.svelte` + `JobPage.svelte` + their tests (addendum 3), `README.md`,
  `docs/Architecture.md` where the build differs, `docs/findings/`, `KICKOFF-*`, STORY-012's status lines.
- Do not touch: the engine repo, `~/Documents/AI/Inkwell`, `~/Documents/Vaults`, `docs/loop-state.json`, the PRD,
  existing tests' assertions (add, don't rewrite), the cut/analyze tasks, the review editor.
- Out of scope: captcha / proof-of-work; Caddy (STORY-013); terms/privacy text (STORY-014); the engine bump
  (STORY-016); any change to the ranges mode.

## Stopping conditions (BLOCKED protocol)

- An AC can't be met without changing the Architecture beyond what § janitor / § Job states / ADR-007 already say
  (name the section).
- A pre-existing test (web or Python) fails for reasons unrelated to your change.
- `bun install` / `uv sync` needs the network and it is unavailable.
- You'd need credentials, cloud resources or money (Q-3's caps stand by default — not a blocker).

## Final report shape

Per-AC ✅/❌ with file:line (the three addenda as their own lines); counts (`uv run pytest` before 367 / after N;
`bun run test` before 274 / after N); `bun run check` and `bun run build` (bundle size); the live run's observations
(the 3rd upload → 429 + `Retry-After`; an expired job's dir gone after the janitor and the SPA's deleted screen;
`queue_position` on a queued job; a failed cut re-cut from the same page); the commits (on both remotes); decisions
taken (salt derivation, trusted-proxy rule, window semantics, janitor placement); what STORY-016 should know.
