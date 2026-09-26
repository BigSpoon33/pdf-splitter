# Findings — STORY-012
**Date:** 2026-09-26
**Status:** done

Commit `7e5af5e feat: STORY-012 - rate limit, disk guard, 24 h janitor and queue position` on `feature/mvp`, pushed to
`origin` and `gitea`. Lines below are as of that commit; § Gate r1 fixes (below) is as of the fix commit
`fix: STORY-012 - gate r1: atomic rate window across midnight, output budget fits the sandbox, PUT write race is 410`
(`docs/findings/STORY-012-review.md` round 1, 4 confirmed), and § Gate r2 fixes as of `fix: STORY-012 - gate r2: the
rate clock is read under the lock; PyMuPDF's file-too-large is the output cap too` (round 2, 2 confirmed).

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
**Command:** `uv run pytest -q` — **Result:** pass — `411 passed in 84.52s` (was 374); after the gate r1 fix
`420 passed in 90.48s`.
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
  threads; a multi-process deployment must set the variable (README, config.py). ~~The window restarts at 00:00 UTC.~~
  (gate r1 finding 2 — it no longer does, see § Gate r1 fixes.)
- **Trusted-proxy rule:** `X-Forwarded-For` is read only when `request.client.host == PDFSPLIT_TRUSTED_PROXY`, and then
  its LAST hop (the one the proxy appended); anything else is client-supplied and ignored. Unset → the peer.
- **Janitor placement:** its own daemon thread in the worker process (`Runner.serve`), own `Store`, sweep at start
  and every 300 s, so a long removal never delays a claim; the `_maybe_sweep` shape (throttled from the loop) was
  the alternative. Orphan directories get a 1 h grace on their mtime because `upload.py` creates the directory
  BEFORE the row exists (the copy + preflight happen in between). `rate` rows are pruned once out of the window
  (nothing reads them after that), `deleted` rows after 7 days, directories always before rows.
- **Output budget:** `min(PDFSPLIT_MAX_OUTPUT_BYTES, max(10 × upload bytes, 256 MB))` (gate r1: and the sandbox
  ceiling `RLIMIT_FSIZE − 64 MiB`, see § Gate r1 fixes); the floor keeps a small book
  whose every section re-embeds its fonts from being refused. Counted as the on-disk size of `work/*.pdf` after each
  section (engine tick / PyMuPDF save), so both paths and stale files count the same; on abort `_reset_outputs`
  empties the section files (the index cache stays), the previous `result.zip` stays downloadable.
- **Vanished files:** `routes/common.vanished` re-reads the row: `deleted`/gone → 410 (+ rmtree of anything a late
  write brought back, like `preview.settled`), otherwise 409 `not_ready` — a legitimately missing file keeps its code.
  `get_analysis`/`get_plan` now return the bytes (`Response`) instead of `FileResponse`, `get_result` streams an open
  handle with `Content-Length`/`Content-Disposition` in Starlette's own format (the disposition test still passes).
- **`Review.svelte` got one line** (outside the authority list, in service of addendum 3): a save from a failed cut
  calls `onsaved` like a save from `done`, so the status card shows `review` after the edit.

## Gate r1 fixes

Round 1 of the review (`docs/findings/STORY-012-review.md`) confirmed four findings; fixed forward in ONE commit on the
`feature/mvp` tip. Each fix has a test that was run against a `git worktree` of `7e5af5e` with the new test files copied
in: 8 of the 9 new tests fail there (the 9th, `test_a_plan_write_that_fails_on_a_live_job_is_still_500`, pins behaviour
the fix must NOT change and passes on both). `uv run pytest -q` → **420 passed** (was 411), `uv run ruff check` clean.
No web change (SPA baselines unchanged: 284 tests, 0/0 check, 109.15 kB build).

1. **Atomic rate check** (finding 1, `upload.py:121-128`): `store.py:267` `Store.take_rate_slot(hashes, since=, limit=,
   now=) -> retry_after | None` counts the window and inserts the hit in ONE `BEGIN IMMEDIATE` transaction (a refusal
   `ROLLBACK`s, nothing recorded); `ratelimit.py:56` `take_slot(store, ip, per_hour, now=, secret=) -> (today's hash,
   retry_after | None)` is the only caller, from `upload.py:122` `_accept`. `ratelimit.retry_after`/`record` and
   `Store.rate_hits` are gone (they WERE the finding). **Choice documented:** the slot is claimed BEFORE the disk guard,
   so an attempt the guard refuses (503) still spends one of the client's `RATE_PER_HOUR` — a client retrying into a
   full disk burns its hour; the alternative (guard first) would let a burst through the guard's own window.
   Tests `tests/test_limits.py:123` `::test_the_slot_is_taken_atomically_so_a_burst_from_one_client_gets_exactly_the_limit`
   (12 stores on 12 threads behind a `Barrier`, limit 6 → 6 slots, 6 × 3600 s, 6 rows) and `:157`
   `::test_parallel_uploads_from_one_client_get_exactly_the_limit` (the same burst through `POST /api/jobs` on a
   `TestClient`: six 201s, six 429s, six directories — on `7e5af5e` it reads `At index 6 diff: 201 != 429`, the review's
   10/10). `::test_rate_window_slides_and_says_how_long_to_wait` was rewritten around `take_rate_slot` (same semantics,
   plus "a refusal records nothing").
2. **Window survives midnight** (finding 2, `ratelimit.py:35-38`): `ratelimit.py:47` `window_hashes(ip, now, secret)`
   returns today's hash, plus yesterday's while `now − 1 h` falls on the previous UTC date; `take_slot` counts under
   both and records under today's (`hashes[0]`). `ratelimit.py:25` `utcnow()` is the api's clock for the window (the
   one seam a test freezes; every function still takes `now`). Tests `test_limits.py:180`
   `::test_window_hashes_reach_into_yesterday_only_during_the_first_hour`, `:190` `::test_the_window_survives_midnight`
   (frozen clock 23:55 → 00:06 → 00:07 at `rate_per_hour=2`: the third upload is 429 with `Retry-After: 2880`, still
   429 at 00:54:59 (`Retry-After: 1`), 201 at 00:55:00; the `rate` rows carry two hashes — the salt turned once — and
   the two accepted jobs' `ip_hash` differ). Architecture ADR-007 "As built" no longer says the window restarts at
   00:00 UTC; it describes the two-hash count and the atomic slot.
3. **Budget fits the sandbox** (finding 3, `cut.py:48-49` vs `sandbox.py:20`): `cut.py:49-50` `ZIP_MARGIN` = 64 MiB,
   `OUTPUT_CEILING = FSIZE_BYTES − ZIP_MARGIN` with `FSIZE_BYTES` IMPORTED from `worker/sandbox.py` (one constant);
   `:57` `output_budget` = `min(setting, OUTPUT_CEILING, max(10 × upload, 256 MB))` — 960 MiB under the defaults
   (`config.py:30-33` and the README row say so). `cut.py:117` `_within_budget(work)` (a context manager around both
   cut paths and the packaging) maps an `OSError(EFBIG)` to `OutputTooLarge` after `_reset_outputs` — a section or
   the ZIP the sandbox refuses is the same failure as the budget, never `resources`; `:229` `package(job_dir, rows)`
   wraps `write_zip` with it and `task.py:106` `run_cut` calls it (`write_zip` itself is unchanged, its `.tmp` cleanup
   and `::test_write_zip_removes_a_tmp_cut_short_by_rlimit_fsize` stand — through `guarded` alone an EFBIG is still
   `resources`, as for the analyze task's writes). Tests `tests/test_cut.py:137`
   `::test_output_budget_never_exceeds_the_sandbox_file_limit` (the ceiling is the sandbox constant less the margin;
   every setting/upload pair lands at or under it), `:236` `::test_a_zip_the_sandbox_refuses_is_too_large_output_and_leaves_no_section`
   (real EFBIG: `package` under `--fsize 4096` → `too_large_output`, `work/` has no PDF, no `.tmp`, previous ZIP kept),
   `:258` `::test_runner_fails_a_cut_whose_zip_hits_rlimit_fsize_as_too_large_output` (the real runner + task with
   `write_zip` forced to EFBIG inside the sandboxed process → row `failed/cut/too_large_output` with the message,
   `work/` empty, previous ZIP kept, then re-cut to `done` from `failed`). One existing assertion changed because the
   finding contradicts it: `::test_output_budget_is_ten_uploads_within_a_floor_and_the_ceiling`'s
   "never past the ceiling" row now expects `OUTPUT_CEILING` (and the 10× row uses a 50 MB upload, since 1000 MiB is
   over the ceiling). Architecture § Job states "Output budget" carries the ceiling and the EFBIG rule.
4. **PUT /plan write race** (finding 4, `routes/plan.py:92`): `routes/plan.py:93-101` wraps the `write_json` of
   `plan.json` — a `FileNotFoundError` re-checks the row through `vanished()`: 410 `expired` when the row is
   deleted/gone (and any late-write directory is removed again); for a live row the original error propagates (500
   `internal`, unchanged — a directory that vanished without a DELETE is a server fault, not the job's expiry). Tests
   `tests/test_api_e2e.py:358` `::test_a_plan_saved_after_a_delete_is_410_not_500` (the REAL DELETE hooked after
   `validate_plan`: 410, no directory, row `deleted`) and `:376` `::test_a_plan_write_that_fails_on_a_live_job_is_still_500`.

## Gate r2 fixes

Round 2 (`docs/findings/STORY-012-review.md`, re-review of `76940a7`) confirmed two incomplete fixes; fixed forward in
ONE commit on the `feature/mvp` tip, `fix: STORY-012 - gate r2: the rate clock is read under the lock; PyMuPDF's
file-too-large is the output cap too`. Each fix has tests that were run with the `76940a7` source tree under the new
test files: all six fail there (the two rate tests with the reviewer's 12/12, the three sandbox tests with `resources`
in place of `too_large_output`, the unit test on `hit_file_limit` at import). `uv run pytest -q` → **426 passed** (was
420), `uv run ruff check` clean. No web change (`bun run test` 284 pass; check/build baselines stand).

1. **The clock is read under the lock** (finding 1, `ratelimit.py:62-64` + `store.py:275-277`): `store.py:272`
   `Store.take_rate_slot(window, *, limit, clock=utcnow) -> (hash, retry_after | None)` takes the write lock FIRST
   (`BEGIN IMMEDIATE`), then reads `clock()` (`:290`) and asks `window(now)` for the hashes to count and the `since`
   the window starts at — so the dated hash set is built from the moment the request commits, never from the moment it
   arrived. `store.py:45` `utcnow()` is that clock; `ratelimit.py:20` re-exports it (`from .store import Store,
   utcnow`), so `ratelimit.utcnow` stays the one seam a test freezes. `ratelimit.py:53` `take_slot(store, ip, per_hour,
   *, secret=, clock=)` builds the window (`window_hashes(ip, now, secret)`, `now − 1 h`) inside the store's
   transaction and passes `clock or utcnow` looked up at call time; its `now=` parameter is gone (nothing passed it).
   `upload.py:122` is unchanged. Tests `tests/test_limits.py:223`
   `::test_take_rate_slot_reads_its_clock_and_builds_the_window_inside_the_transaction` (the clock is called once per
   attempt with `conn.in_transaction` True; the window and the hit's `at` are that reading) and `:248`
   `::test_a_request_that_wins_the_lock_after_midnight_counts_under_that_days_hashes` — the reviewer's interleaving,
   deterministic: a frozen `ratelimit.utcnow` answers with the request's arrival (00:00:00.05 for six, then 23:59:59.9
   for six) while no transaction is open and with the moment the lock was won (00:00:00.05) once one is; on `76940a7`
   the second six read on arrival, look under yesterday's hash alone and get through (12/12), now all twelve read
   under the lock and exactly six get a slot (six × `Retry-After` 3600, six rows under today's hash). The `slot()`
   helper (`:100`) moved to the new signature; every r1 assertion stands (burst, midnight, sliding window).
2. **PyMuPDF's file-too-large is the output cap** (finding 2, `cut.py:116-130`): a section write that hits RLIMIT_FSIZE
   inside MuPDF is `FzErrorSystem('code=2: cannot fwrite: File too large')` — no errno, and the same `code=2` prefix
   as MuPDF's allocator failing, so `guarded` read it as `resources` and the truncated section stayed in `work/`.
   `cut.py:61` `hit_file_limit(e)`: an `OSError` with `errno.EFBIG`, or a MuPDF error (`analyze.MUPDF_ERRORS`, both
   raise paths) whose message carries libc's words for EFBIG (`:54` `MUPDF_FILE_TOO_LARGE`, built from
   `os.strerror(errno.EFBIG)` — the same libc MuPDF asked); `:130` `_within_budget` catches `(OSError, *MUPDF_ERRORS)`
   and converts only those (`:140`) into `OutputTooLarge` after `_reset_outputs`; anything else re-raises, so the
   allocator's `calloc … failed` still reaches `task.is_mupdf_alloc_failure` → `resources`. `task.py` is unchanged.
   Tests `tests/test_cut.py:291` `::test_hit_file_limit_tells_the_sandboxs_efbig_from_an_allocator_failure`, `:312`
   `::test_a_section_the_sandbox_refuses_is_too_large_output_on_both_paths[chapters|ranges]` (the real sandbox with
   `fsize` = half the smallest section, `cut_book` and `cut_ranges` under `guarded` → `too_large_output`, no PDF and
   no manifest in `work/`, the index cache byte-identical) and `:342`
   `::test_runner_fails_a_cut_whose_section_hits_rlimit_fsize_as_too_large_output` (the real runner + task on a
   120-page ranges job with `sandbox.limits` patched to `fsize` 128 KiB — over the task's own row writes, under one
   ~500 KB span: row `failed/cut/too_large_output` with the message, `work/` empty, previous ZIP kept, re-cut to
   `done` under the real limits). A first draft of that runner test at 16 KiB failed on SQLite instead (the fixtures'
   WAL is already ~40 KB before the task starts and appends hit EFBIG at the first progress write) — hence the big
   book, and why the runner-level test covers one path: the row plumbing past `guarded` is path-independent.

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
