# Findings — STORY-012
**Date:** 2026-09-26
**Status:** done

Commit `7e5af5e feat: STORY-012 - rate limit, disk guard, 24 h janitor and queue position` on `feature/mvp`, pushed to
`origin` and `gitea`. Lines below are as of that commit.

## AC Verification
- [x] AC-1: sliding-window rate limit — `src/pdf_splitter/ratelimit.py` (`client_ip` :24, `ip_hash` :35, `retry_after`
  :41, `record` :51, `WINDOW` :19 = 1 h); wired in `src/pdf_splitter/upload.py:120-128` (`_accept`, before `mkdir`), 429 via
  `reject(code, headers)` :63 with `Retry-After`; codes in `src/pdf_splitter/errors.py:25-26`; store side
  `src/pdf_splitter/store.py:262-274` (`add_rate`, `rate_hits`, `prune_rate`) + index `rate_window` :33. Settings
  `config.py:26` `trusted_proxy`, `:29` `ip_salt`. Tests `tests/test_limits.py::test_client_ip_believes_x_forwarded_for_only_from_the_trusted_proxy`,
  `::test_ip_hash_is_salted_shared_by_secret_and_rotates_daily`, `::test_rate_window_slides_and_says_how_long_to_wait`,
  `::test_seventh_upload_in_an_hour_is_429_with_retry_after`, `::test_rate_rows_and_job_rows_carry_the_same_hash_and_never_the_ip`.
- [x] AC-2: disk guard — `upload.py:41` `disk_full(settings)` (`shutil.disk_usage(jobs_dir).free < min_free_gb × 1e9`),
  503 at `:127`; `config.py:24` `min_free_gb = 2`. Tests `test_limits.py::test_upload_refused_with_503_when_the_volume_is_nearly_full`
  (monkeypatched `upload.shutil.disk_usage`, nothing on disk), `::test_the_guard_is_min_free_gb_in_decimal_gb_like_health`.
- [x] AC-3: janitor — `src/pdf_splitter/janitor.py` `sweep(store, settings, now)` :33 (expired rows → `deleted` first
  (:56) then dir; `deleted` rows' dirs :42-45 — addendum 1; orphan dirs older than `ORPHAN_GRACE` (:29, 1 h) via
  `_reap_orphans` :75; `rate` rows out of the window + `deleted` rows older than `KEEP_DELETED` (:28, 7 d) :47-48); run by
  `worker/runner.py:197` `_janitor_loop` on its own thread (`serve` :228, at start and every `JANITOR_EVERY_S` = 300 s :37). Tests
  `test_limits.py::test_expired_jobs_are_marked_deleted_row_first_and_their_directories_removed`,
  `::test_a_running_job_past_its_ttl_loses_its_row_before_its_directory`, `::test_a_deleted_rows_directory_that_came_back_is_removed_again`,
  `::test_orphan_directories_are_removed_after_a_grace_and_the_database_never`, `::test_rate_rows_out_of_the_window_and_week_old_deleted_rows_are_pruned`,
  `::test_one_failure_does_not_stop_the_pass`, `::test_the_worker_runs_the_janitor_on_start_and_every_interval`.
- [x] AC-4: `queue_position` — `store.py:139` `Store.queue_position(job)` (1 + queued rows of the same kind ahead in
  `claim_next`'s order; rowid breaks same-second ties, `claim_next` :164 now orders `created_at, rowid` too);
  `routes/plan.py:40` `status_of(job, store)`, `:61` `get_job`. Test `tests/test_api_e2e.py::test_queue_position_counts_the_same_kind_ahead_in_fifo_order`
  (parametrized a-second-apart / same-second). The SPA needed no change (`JobStatus.svelte:94`).
- [x] AC-5: every test above runs on a frozen clock (`now=` everywhere, `os.utime` for orphan ages); the disk guard
  monkeypatches `shutil.disk_usage` where `upload.py` looks it up. `tests/test_limits.py` (14 tests) + additions listed.
- [x] Addendum 1 (deleted rows' dirs): `janitor.py:39-42`, test `::test_a_deleted_rows_directory_that_came_back_is_removed_again`.
- [x] Addendum 2 (vanished job file → 410): `routes/common.py:66` `vanished(store, settings, job)` (re-reads the row,
  rmtree if `deleted`, 410 when gone else 409), `:78` `job_file` (read, never `exists()`-then-read, via the patchable
  `:62` `read_bytes`), `:86` `saved_plan(store, settings, job)`; `routes/plan.py` `get_analysis`/`get_plan`/`put_plan`
  read through it; `routes/download.py:37` `_result_zip` opens the ZIP (`:33` `open_result`) — `get_result` streams the
  open handle, `get_manifest`/`get_section` read the open handle. Tests `test_api_e2e.py::test_a_job_file_deleted_after_the_row_check_is_410_not_500`
  (6 routes), `::test_an_output_deleted_after_the_row_check_is_410_not_500` (3 routes), `::test_a_missing_job_file_on_a_live_job_is_still_409`.
- [x] Addendum 3 (recoverable failed cut): `routes/common.py:51` `editable(job)` (state AND kind), `:57` `require_editable`;
  `routes/plan.py:93-96` `put_plan` failed → review; `:108-110` `post_cut`'s second conditional transition with
  `store.py:202` `transition(..., expect_kind=)`. SPA: `web/src/components/Download.svelte:42-45` (Split live, `:104`
  reason above it), `JobPage.svelte:58` (the editor mounts on a reload of a failed cut), `Review.svelte:196` (a save
  from a failed cut re-polls like from `done`). Tests `test_api_e2e.py::test_a_failed_cut_is_recut_from_the_same_page`,
  `::test_a_failed_cut_edited_returns_to_review`, `::test_a_failed_analyze_stays_terminal` (+ the pinned
  `("failed","analyze","not_ready")` row); `Download.test.ts` "a failed cut keeps Split live…" + fallback wording;
  `JobPage.test.ts` "a failed cut keeps the editor and Split…", "…re-cut straight away…", "…reloaded after a failed cut…".
- [x] Addendum 4 (output cap): `worker/cut.py:40-41` `OUTPUT_MULTIPLIER`/`OUTPUT_FLOOR`, `:44` `OutputTooLarge`, `:48`
  `output_budget`, `:52` `written_bytes`, `:64` `Budget.check` — called from `cut_book`'s progress tick `:121` (after
  every engine section) and `cut_ranges` `:169` (after every span); `_reset_outputs` on abort (`:129`, `:183`);
  `worker/task.py:97` `run_cut` computes the budget from `settings.max_output_bytes` and the row's `bytes`, `:122`
  `guarded` → `too_large_output`; `worker/runner.py:43` `TASK_CODES`, `:47` `MESSAGES["too_large_output"]`, the env
  passes `PDFSPLIT_MAX_OUTPUT_BYTES` to the task `:126`; `config.py:31` `max_output_bytes` 2 GiB; SPA `api.ts:8`.
  Tests `tests/test_cut.py::test_output_budget_is_ten_uploads_within_a_floor_and_the_ceiling`,
  `::test_cut_book_stops_at_the_budget_and_leaves_no_section_behind`, `::test_runner_fails_a_cut_over_the_budget_as_too_large_output_then_recuts_it`;
  `tests/test_ranges.py::test_cut_ranges_stops_at_the_budget_and_leaves_no_span_behind` (200 whole-book spans),
  `::test_a_ranges_cut_over_the_budget_fails_cleanly_and_a_smaller_plan_cuts_from_the_same_page`;
  `test_worker.py::test_guarded_maps_exceptions` + `::test_classify` rows.

## Test Results
**Command:** `uv run pytest -q` — **Result:** pass — `411 passed in 84.52s` (was 374).
**Command:** `uv run ruff check` — **Result:** pass — `All checks passed!`
**Command:** `cd web && bun run test` — **Result:** pass — `Test Files 21 passed (21) · Tests 284 passed (284)` (was 280).
**Command:** `cd web && bun run check` — **Result:** pass — `337 FILES 0 ERRORS 0 WARNINGS`.
**Command:** `cd web && bun run build` — `dist/assets/index-CTEkmUWg.js 109.15 kB │ gzip: 38.89 kB` (was 109.1 / 38.9).

**Live run** (API :8010 + worker, `PDFSPLIT_RATE_PER_HOUR=2`, Vite :5181, headless Chromium via Playwright):
- Uploads A, B → 201; the 3rd → `429 {"code":"rate_limited",…}` with `retry-after: 3600` and an `x-request-id`; the log
  line carries only the hash prefix (`upload refused: rate limited (4811dd63, retry after 3600s)`).
- Worker stopped, `POST /cut` on A then B → A `queue_position 1`, B `2` (both `queued/cut`).
- Worker with `PDFSPLIT_MAX_OUTPUT_BYTES=1` → A `failed/cut/too_large_output`, message "The cut would write more than the
  output limit. Split into fewer or smaller sections."; A's dir holds `analysis.json plan.json source.pdf work`, 0 PDFs
  in `work/`, no `.tmp`, `result.zip` → 409.
- SPA `/j/A` (reload on the failed cut): status "Failed" + the message, the Sections editor mounted, `Split into 3 PDFs`
  enabled, the reason above it ("The last cut failed: … Change the sections if you need to, then split again.").
- Worker restarted without the cap; `POST /cut` on A from `failed` → 202 → `done` 3/3 in 4 s, `result.zip` 94,778 B,
  manifest rows 0–2.
- B's `expires_at` moved to 2026-09-25 in `jobs.db` at 09:16:28 UTC: GET → 410 at once (the row), the directory went at
  the janitor's next pass exactly 300 s after the worker start (`janitor: 1 expired, 0 recreated, 0 orphans, 0 rate,
  0 rows`), row `deleted`; SPA `/j/B` → the deleted screen ("This job was deleted", `data-gone="expired"`).

## Bugs Found
- **Same-second uploads both read `queue_position` 1** (found in the live run: timestamps are whole seconds, so
  `created_at <` counted nobody). Fixed before the commit: `queue_position` and `claim_next` break the tie on `rowid`
  (`store.py:139-150`, `:164`); `test_queue_position_counts_the_same_kind_ahead_in_fifo_order[same-second]`.
- **A failed cut showed no editor and no Split after a reload** (found in the live run): `JobPage.svelte` only mounted
  Review for `review`/`done` or an active cut. Fixed: any `kind: cut` status mounts it (`:58`);
  `JobPage.test.ts` "a job reloaded after a failed cut shows the editor, the reason and a live Split".
- Not a bug, noted: `Download.test.ts` "a failed cut disables Split…" pinned the old dead end and was replaced (the
  addendum reverses that behaviour); `errors.test.ts`'s sanity count went 14 → 16 for the two new codes.

## Decisions
- **What the window counts:** every `POST /api/jobs` attempt that passes both guards, recorded before the body is
  copied — a refused file (not a PDF, too large) still cost a copy and a preflight subprocess. Only the `file is None`
  400 is before the checks. Order: rate → disk → record → `mkdir`.
- **Window semantics:** sliding hour, `at > now − 1 h` (a hit exactly an hour old has left); `Retry-After` = whole
  seconds until the oldest of the `RATE_PER_HOUR` newest hits leaves, minimum 1.
- **Salt derivation (ADR-007):** `sha256(sha256("<secret>:<UTC date>") + ip)`; secret = `PDFSPLIT_IP_SALT`, else one
  `secrets.token_hex(16)` per api process. The CLI runs ONE uvicorn process, so the default is shared by all its
  threads; a multi-process deployment must set the variable (README, config.py). The window restarts at 00:00 UTC.
- **Trusted-proxy rule:** `X-Forwarded-For` is read only when `request.client.host == PDFSPLIT_TRUSTED_PROXY`, and then
  its LAST hop (the one the proxy appended); anything else is client-supplied and ignored. Unset → the peer.
- **Janitor placement:** its own daemon thread in the worker process (`Runner.serve`), own `Store`, sweep at start
  and every 300 s, so a long removal never delays a claim; the `_maybe_sweep` shape (throttled from the loop) was
  the alternative. Orphan directories get a 1 h grace on their mtime because `upload.py` creates the directory
  BEFORE the row exists (the copy + preflight happen in between). `rate` rows are pruned once out of the window
  (nothing reads them after that), `deleted` rows after 7 days, directories always before rows.
- **Output budget:** `min(PDFSPLIT_MAX_OUTPUT_BYTES, max(10 × upload bytes, 256 MB))`; the floor keeps a small book
  whose every section re-embeds its fonts from being refused. Counted as the on-disk size of `work/*.pdf` after each
  section (engine tick / PyMuPDF save), so both paths and stale files count the same; on abort `_reset_outputs`
  empties the section files (the index cache stays), the previous `result.zip` stays downloadable.
- **Vanished files:** `routes/common.vanished` re-reads the row: `deleted`/gone → 410 (+ rmtree of anything a late
  write brought back, like `preview.settled`), otherwise 409 `not_ready` — a legitimately missing file keeps its code.
  `get_analysis`/`get_plan` now return the bytes (`Response`) instead of `FileResponse`, `get_result` streams an open
  handle with `Content-Length`/`Content-Disposition` in Starlette's own format (the disposition test still passes).
- **`Review.svelte` got one line** (outside the authority list, in service of addendum 3): a save from a failed cut
  calls `onsaved` like a save from `done`, so the status card shows `review` after the edit.

## Handoff Context for Next Session
STORY-016 runs in the ENGINE repo (`~/Documents/Repos/monograph-splitter`, branch `feature/web-mode`, tip `8e52cc3`
= v0.4.1) and only then touches this repo (pin bump). Nothing here depends on it except `tests/test_health.py:27`
(pins `engine_version == "0.4.1"`), `pyproject.toml:12`, `uv.lock`, and Architecture's three `v0.4.1` mentions
(`:28`, `:263`, `:344`). The runner passes `PDFSPLIT_MAX_OUTPUT_BYTES` to the task through the environment
(`runner.py:126`): any new task-side setting needs the same. `pgrep -f "pdf-splitter worker"` matches the shell
that runs it — list first, kill explicit PIDs (the live run lost a shell to `kill $(pgrep -f …)`).

## Out-of-Scope Items
- `queue_position` is per kind (the claim order): a queued cut waits behind every queued analyze too (`run_once`
  tries `analyze` first), which the number does not show. Fine at 2 workers; a global position would need the
  runner's kind order in SQL.
- Multi-process api (uvicorn `--workers`) needs `PDFSPLIT_IP_SALT` set or each process has its own window — documented,
  not enforced (the CLI is single-process).
- The rate window counts attempts, not accepted uploads; a captcha / proof-of-work stays out (story Out of Scope).
- `get_analysis` reads the whole file into memory (a few MB at 2,000 pages) instead of `FileResponse`; streaming an
  open handle like `get_result` is the alternative if it ever matters.
- The janitor's start-up sweep runs on every worker start; a fleet of workers would each sweep — idempotent, so
  harmless, but STORY-013's compose has one worker anyway.
- A scratch `jobs.db` from an earlier live run in the same scratchpad path held a `deleted` row from 2026-09-25 with no
  directory; the janitor treats it correctly (pruned after 7 days) — nothing to do.
