# KICKOFF — STORY-007: Web: plan, preview, cut and download API

## What you're walking into

"pdf-splitter" is a public site: someone drops in a big PDF and gets back one PDF per chapter or section.
The product lives in two repos:

- **Web (you write code here):** `~/Documents/Repos/pdf-splitter` (GitHub `BigSpoon33/pdf-splitter` = `origin`,
  Gitea mirror = `gitea`), branch **`feature/mvp`**. Stay on that branch. STORY-006 landed as
  `12b4e9b feat: STORY-006 - sandboxed worker runner and analyze task (outline + heading candidates)`, its docs
  commit, the gate r1 fix `e15e7f0 fix: STORY-006 - gate r1: MuPDF allocation failures are resources; analysis JSON
  survives bad text`, the gate r2 fix `5f15a36 fix: STORY-006 - gate r2: MuPDF allocator failures are resources
  whatever the exception type; a real mid-write failure test`, and each fix's docs commit. Anything after those is
  the orchestrator's gate work. Check `git log --oneline -8`. **Baseline: 207 tests pass (`uv run pytest`, ~55 s,
  because `tests/test_worker.py` runs real subprocesses, deliberate timeouts and two MuPDF memory bombs), and
  `uv run ruff check` is clean.**
  `docs/loop-state.json` belongs to the orchestrator, so never stage it.
- **Engine (read-only here):** `~/Documents/Repos/monograph-splitter` (GitHub `BigSpoon33/pdf-splitter-engine`),
  pinned at tag `v0.4.1` in `pyproject.toml`/`uv.lock`. The surface you need:
  - `monograph_splitter.profile`: `profile_from_dict(d, base=WEB_BASE)` (only `WEB_KEYS`; unknown key or bad
    type/range → `ProfileError`), `WEB_BASE`, `WEB_KEYS`.
  - `monograph_splitter.session.Book.open(pdf=, out=, profile=, entries=list[dict], log=…)`. It indexes into
    `out/.book-index.json` (cached, keyed on file size, pages, `ENGINE_VERSION` and `profile.sha256`), and
    `Book.cut_all(progress=None, verify=True, …) -> {written, flags, notes, leaks, missing, unknown}` calls
    `progress(done, total, name)` after each entry. Entry names containing `/`, `\`, NUL, or equal to `.`/`..`/empty
    raise `ValueError`. Also read `session.py`: `planned(entry, override)` + `rects(p)` (the Section plan's inputs;
    Architecture's `_plan_view` lives in `review/server.py:109`), `set_override`, and `sheet_png(sheet, dpi)`
    (cached under `out/.sheets/<dpi>/`).
  - The engine's `import fitz` prints a deprecation notice to **stdout**, and its default `log=print` does too.
    Pass `log=lambda *_: None` and keep results on the last stdout line.
- Read, in order: `docs/stories/STORY-007.md` (its ACs are authoritative), `docs/Architecture.md` § api, § Data
  Types (Plan, Section plan, Manifest, plus the `NNN-<slug>` engine-name rule under the table), § API Interface,
  § Job states, § File layout, then `docs/findings/STORY-006-findings.md` and `docs/findings/STORY-005-findings.md`.

Toolchain: `uv 0.12.10`, Python 3.12. Dev deps: pytest, ruff and `httpx2`.

## What STORY-006 established (use these, don't re-invent)

- **The worker** `src/pdf_splitter/worker/runner.py`:
  - `Runner(settings, task=task_args, kinds=("analyze",), poll=1.0)`. `serve(stop_event)` inits the store, runs
    `recover`, and then `settings.workers` claim loops, each with its own `Store`. `run_once(store)` claims one job
    of each kind in `kinds` order and `execute(store, job)`s it.
  - **Adding `cut`:** pass `kinds=("analyze", "cut")` in `cli.py:29` (the default is analyze-only).
    `Runner.timeout("cut")` already returns `settings.cut_timeout`. The command is
    `sandbox.command(sandbox.limits(timeout), task_args(kind, id))` =
    `[python, -m, pdf_splitter.worker.sandbox, --as, 2147483648, --cpu, <timeout+10>, --fsize, 1073741824, --, -m,
    pdf_splitter.task, <kind>, --, <id>]`. The launcher sets the rlimits and `execv`s. The child runs in its own
    session with `PDFSPLIT_JOBS_DIR` set to the runner's `settings.jobs_dir`. Contract:
    `tests/test_worker.py::test_runner_command_limits_and_wall_timeout`.
  - **Failure mapping** (`classify`, `execute`): a wall timeout → `killpg` → `failed/timeout`; SIGXCPU/SIGKILL/
    SIGXFSZ or a `{"ok": false, "code": "resources"}` result → `failed/resources`; anything else (including exit 0
    with the row still `running`) → `failed/internal`. Each gets a user-facing `message` from `runner.MESSAGES`.
    The `resources` result covers MuPDF's own allocator failing under RLIMIT_AS (gates r1+r2: a `RuntimeError`
    or a `pymupdf.mupdf.FzErrorSystem` saying `code=2: calloc (…) failed`, never a `MemoryError`), so a cut on a
    hostile PDF reads as `resources` too.
    Failures are written with `Store.transition(id, "running", "failed", …)`, so a deleted job is never resurrected.
    The stderr tail is logged via `loggable_tail` (redacted with `access_log.redact_path`, then JSON-escaped).
    Contracts: `::test_classify`, `::test_runner_failure_mapping`, `::test_the_loop_keeps_running_after_failed_jobs`.
  - **Re-queue** (`recover`, at start and every 30 s): a job `running` past its kind's timeout → `queued` with
    `error_code='requeued'`, and the second time → `failed/timeout`. **So a `queued`/`running` row can carry
    `error_code='requeued'`.** `GET /api/jobs/{id}` should expose `error_code` only when `state == 'failed'`.
    `POST /cut` must reset it: `store.set_state(id, "queued", kind="cut")` clears `error_code`/`message`.
    Contracts: `::test_restart_requeues_a_stale_running_job_once_then_fails_it`,
    `::test_a_requeued_job_that_succeeds_clears_the_marker`.
- **The task** `src/pdf_splitter/worker/task.py` (`python -m pdf_splitter.task` is the shim `src/pdf_splitter/task.py`):
  - `main(argv)`: `kind` choices are `["analyze"]`, so add `"cut"`. It refuses ids that aren't a plain path
    component, turns MuPDF display errors off, and runs the job inside `guarded(fn)`. `guarded` prints the result
    line and returns the exit code: `MemoryError`/`OSError(EFBIG)`, or any exception for which
    `task.is_mupdf_alloc_failure(e)` is true → `resources` (exit 3); any other exception → `internal` (exit 1,
    traceback to stderr); `fn()` returning False → `internal`. **The MuPDF rule is by MESSAGE, not type** (gate
    r2): `e` must be a `RuntimeError` or a `pymupdf.mupdf.FzErrorBase` subclass (`analyze.MUPDF_ERRORS`; PyMuPDF's
    `_extra` helpers raise the former, its raw bindings such as `fz_load_page` the latter) AND `str(e)` must match
    `task.MUPDF_ALLOC_FAILED` (`code=2…` / `calloc|malloc|realloc … failed` / `out of memory`). A `FzErrorFormat`
    or a `ValueError` with the same words stays `internal`. The cut task inherits this rule for free by running
    inside `guarded`. Don't catch MuPDF errors yourself inside `run_cut`: let them reach `guarded`, or the
    mapping is lost — and if you must catch a MuPDF failure locally (a UI-only value, like `page_labels` does),
    catch `*analyze.MUPDF_ERRORS`, never bare `RuntimeError`. Write `run_cut(settings, job_id, store) -> bool` in
    the shape of `run_analyze`: check the row is `running` with the right kind, work, then
    `store.transition(id, "running", "done")`. Contracts: `::test_guarded_maps_exceptions` (17 cases),
    `::test_real_task_on_a_mupdf_memory_bomb_is_resources` (the fixture `tests/fixtures/hostile.py::xobj_bomb`, a
    4.9 KB PDF that hits 2 GB inside text extraction → RuntimeError) and `::test_real_task_on_a_link_uri_bomb_is_resources`
    (`hostile.py::link_uri_bomb`, 309 KB, fails inside `fz_load_page` → `FzErrorSystem`, ≈ 2 s; the cheaper one to
    reuse for a cut-side resources test), `::test_page_labels_survive_a_raw_mupdf_error`,
    `::test_task_skips_a_job_that_is_not_running`.
  - `Throttle(write)`: progress writes at most every 0.5 s, and a message change waits out the window instead of
    being dropped. Reuse it for `cut_all(progress=…)` (its callback is `(done, total, name)`, so map it to
    `(done, total, "Cutting")`). Contract: `::test_throttle_writes_at_most_every_half_second`.
  - `_write_json(path, data)` is write-then-rename, encodes with `errors="replace"` so it can't fail on a
    string, and removes the `.tmp` on any failure (gate r1). Use the same pattern for `result.zip` and
    `manifest.json` (write `<name>.tmp`, then `os.replace`, and unlink the `.tmp` in an `except`). Contracts:
    `::test_write_json_never_fails_on_encoding`, `::test_write_json_removes_the_tmp_when_the_rename_fails`,
    `::test_write_json_removes_a_tmp_cut_short_by_rlimit_fsize`. **Test the zip's cleanup the same way** (gate
    r2 rejected a test that failed before the `.tmp` existed): make the failure happen AFTER the `.tmp` is on
    disk — `os.replace` monkeypatched to raise, or a real EFBIG under `sandbox.command({**sandbox.limits(10),
    "fsize": 4096}, …)` — assert the previous file is intact and no `.tmp` remains, and check the test fails
    with the cleanup removed before you commit.
- **The analysis/plan files** (`src/pdf_splitter/worker/analyze.py`): `<id>/analysis.json` (Architecture shape,
  keys pinned by `::test_analyze_outline_book`, `::test_analyze_headings_book_without_outline`) and
  `<id>/plan.json` = `default_plan(analysis)`: `{source, settings, sections: [{name, page, heading}], overrides:
  {}}`. `settings` = `analyze.DEFAULT_SETTINGS` (the 5 Plan keys at `WEB_BASE` values). `suggested` can be
  `{"source": "manual", "level": null}` with `sections: []`. Contracts: `::test_default_plan_from_outline_and_headings`,
  `::test_default_plan_settings_round_trip_and_match_the_index_profile`,
  `::test_task_main_writes_analysis_and_plan_then_review` (`plan.json == default_plan(analysis.json)`). Pages are
  1-based sheets (ADR-003). Plan `sections[].name` is the display name. The engine gets `NNN-<ascii-slug>` (see
  Architecture § Data Types). **Every string in `analysis.json` (and so in the default `plan.json`) is already
  UTF-8-safe**: `analyze.json_safe` (gate r1) replaces the lone surrogates PyMuPDF's `surrogateescape` decoding
  leaves in a malformed bookmark title or heading with U+FFFD before the dict is built, so names read from those
  files need no cleaning. Contracts: `::test_task_survives_a_bookmark_title_that_is_not_valid_unicode` (the two
  byte-exact titles from the review, through `task.main`), `::test_json_safe_replaces_every_lone_surrogate_and_nothing_else`.
- **The index cache**: `<id>/work/.book-index.json` is built under `profile_from_dict(DEFAULT_SETTINGS)`. So
  `Book.open(out=<id>/work, profile=profile_from_dict(plan["settings"]))` reuses it only while the settings hash
  matches. Any layout change re-indexes the whole book (Maciocia, 1319 pages: ≈ 12 s), and that matters inside a
  20 s preview timeout. See the findings' Out-of-Scope engine note. Measure before optimising, and don't change the
  engine here.
- **Store** (`src/pdf_splitter/store.py`): new `transition(id, expect, state, *, error_code, message) -> bool`
  (conditional update) and `running_since(kind, before)`. Existing: `claim_next`, `update_progress`, `set_state`
  (overwrites error_code/message; `kind=` switches the kind), `get_job`, `queue_length`, `expired`. One `Store` per
  thread/request. Contracts: `tests/test_store.py`.
- **Sandbox** `src/pdf_splitter/worker/sandbox.py`: `limits(timeout)`, `apply(lim)`, `command(lim, python_args)`.
  **The preview subprocess needs the same limits** (Architecture § api: "same rlimits as the worker"). It is spawned
  from API threadpool threads, so never use `preexec_fn`. Use either `sandbox.command(sandbox.limits(20),
  ["-m", "pdf_splitter.<preview module>", …, "--", <path or id>])` or the preflight's opt-in `--cpu-limit` pattern
  (`preflight.py:64`). Contracts: `::test_sandbox_sets_the_three_rlimits_before_the_task_runs`,
  `::test_preflight_runs_under_rlimits`.
- From STORY-005: `upload.run_preflight` is the pattern for an API-side subprocess (`capture_output`,
  `stdin=DEVNULL`, a timeout, the result on the LAST stdout line, every failure collapsed to one code, only `log_id`
  logged). `upload.reject(code)` returns `{"code", "message"}`. `access_log.redact_path` hashes every 16+ run of
  `[A-Za-z0-9_-]` anywhere in a string plus the `/api/jobs/<segment>` slot; `loggable_path` escapes it. Settings
  paths are always absolute. `deps.SettingsDep`/`StoreDep` (one Store per request).
- Test helpers: `tests/fixtures/books.py` has `headed_book(path, outline=True)` (6 pages; outline level 1 = 3
  chapters on pages 1/3/4, level 2 = 3 sections; with `outline=False` the heading level 1 = the same 3 chapters at
  16 pt) and `text_book(path, pages)`. It is importable as `from fixtures.books import …` (tests/ is on sys.path).
  In `tests/test_worker.py`: `queue_job(settings, store, id, pdf=…)`, `open_store`, `wait_for`,
  `serve_in_thread(runner)`, `assert_id_gone`, and the fake-task pattern (`python -c CODE <id>` through the real
  sandbox, `GUARDED` prefix). Move shared helpers to `tests/conftest.py` or a helper module if you reuse them.
  **AC-6's e2e is synthetic-book-based:** `headed_book` with the headings source gives exactly 3 sections.

## Critical gotchas

1. **The stdout rule.** Every subprocess (the cut task, the preview) reports on its LAST stdout line. PyMuPDF/the
   engine print to stdout mid-run (`fitz` deprecation, `index_book`'s `log=print`, possibly `cut_all`'s own
   logging, so read `session.py` for which calls log and pass a silent `log`).
2. **Job ids never reach logs** (ADR-007). The new routes carry ids in paths (`/api/jobs/<id>/sheets/3.png`). The
   access log already hashes them. Any NEW log line, traceback (AC-7) or stderr tail goes through `redact_path` or
   `runner.loggable_tail`. For AC-7, don't `log.exception` raw: format the traceback and redact it (see
   `runner._loop`). Add an `assert_id_gone` test per new logging path.
3. **`--` before any positional that can start with `-`** (1 in 64 ids). Test with `-bCdEfGhIjKlMnOpQrSt00`.
4. **Path params are ints validated against the job** (`n` in 1..pages, `i` in 0..len(sections)-1). Never join a
   user string into a path. Section PDFs are found through the manifest/plan index, never by name.
5. **Engine names**: `NNN-<ascii-slug>` (≤ 80 bytes, unique). Display names can collide or contain `/`, and the
   engine refuses unsafe names. ZIP entries are `NNN - <sanitized display name>.pdf` (you can reuse
   `upload.sanitize_filename`'s rules, but also strip `/`/`\`). Overrides are keyed by the Plan's section INDEX, so
   translate them to engine names. Names from `analysis.json`/`plan.json` are already JSON-safe (above), but a
   plan a client PUTs is not: JSON `"\udcff"` escapes decode to a lone surrogate that strict UTF-8 (the file
   write, the ZIP entry name, the API response) rejects. Run `analyze.clean_text` over each name in the Plan
   model, or reject such names with 422, and test it with a `"\udcff"` escape in the request body.
6. **State machine** (Architecture § Job states): `POST /cut` only from `review` or `done` (409 otherwise, including
   while `queued`/`running`). `PUT plan` from `done` returns the job to `review`. Old outputs stay downloadable until
   the next cut replaces them, so the cut must write `result.zip` atomically.
7. **`Store.transition`, not `set_state`, for worker-side finishes**, so a job deleted mid-cut stays `deleted`.
   `DELETE` should set `deleted` first and then remove the dir. A running cut task then finds its row not `running`
   and must not recreate files (check before writing the zip).
8. Hide `error_code` unless `failed` (the `requeued` marker, see above).
9. Tests must never touch the real `./jobs`, and must not need the network. Port 8000 is taken on this laptop by an
   unrelated process, so use 8010+ for manual runs, stop your servers by PID (`lsof -ti :8010` then `kill <pid>`),
   and never `pkill -f`.

## Recommended ordering

1. `src/pdf_splitter/models.py`: Pydantic Plan (AC-1 ranges, ≤ 2,000 sections, names ≤ 120 and deduped, pages in
   1..pages), then check `profile_from_dict(plan.settings)` accepts it. The job-status GET (Architecture § API
   Interface) if it doesn't exist yet: it does not. STORY-005 built only `POST /api/jobs`.
2. `routes/plan.py`: `GET/PUT /api/jobs/{id}/plan`, `GET /api/jobs/{id}/analysis` (409 until `review`), and
   `GET /api/jobs/{id}` (hide `error_code` unless failed; 404 unknown, 410 expired).
3. AC-7 request ids + the 500 handler (middleware next to `access_log`), with a forced-raise test route.
4. `worker/cut.py` + `run_cut` in `worker/task.py` + `kinds=("analyze", "cut")` in `cli.py`, and `POST /cut`
   (AC-4). Test it with the real sandboxed runner, like `::test_runner_runs_the_real_analyze_task_to_review`.
5. `routes/download.py` (AC-5), `DELETE`.
6. `routes/preview.py` (AC-2/AC-3): a sandboxed preview subprocess (20 s), a PNG cache per (sheet, dpi,
   settings-hash) at `<id>/png/<dpi>/<sheet>-<hash>.png`.
7. `tests/test_api_e2e.py` (AC-6): upload → `Runner.run_once` (analyze) → PUT plan (headings) → cut → zip with 3
   PDFs + manifest, 0 leaks.
8. `uv run pytest && uv run ruff check`, then a manual end-to-end on port 8010 with `pdf-splitter worker`.

## Conventions

- Python 3.12, `from __future__ import annotations`, type hints, and small modules. Comments explain WHY, never
  WHAT. `ruff check` must be clean (keep lines ≤ 110).
- Commit on `feature/mvp`: `feat: STORY-007 - plan/preview/cut/download API and the cut task`, ending with
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Stage explicit paths only (never
  `git add -A` / `.`, and never `docs/loop-state.json`). Never `reset --hard` / `checkout .`.
- Push: `git push origin feature/mvp` and `git push gitea feature/mvp`.
- Findings and the next kickoff go in `docs/findings/STORY-007-findings.md` and `docs/KICKOFF-STORY-008.md`,
  committed as `docs: STORY-007 - findings + KICKOFF-STORY-008`. Set STORY-007's status lines to Done.

## Authority

- Free: `src/pdf_splitter/` (new `routes/`, `models.py`, `worker/cut.py`, edits to `worker/task.py`, `runner.py`,
  `cli.py`, `app.py`, and `store.py` that keep existing tests green), `tests/`, `pyproject.toml`/`uv.lock` if a
  dependency is truly needed, `README.md`, and in `docs/` only `findings/`, `KICKOFF-*` and STORY-007's status
  lines.
- The engine repo: read-only. If you need an engine change (for example a cache key that ignores web geometry),
  implement without it and record the need in findings.
- Do not touch: `~/Documents/AI/Inkwell`, `~/Documents/Vaults`, `docs/loop-state.json`, and PRD/Architecture
  (report doc errors in findings).
- Out of scope: rate limits, the disk guard, the janitor and queue position (STORY-012), compose/network
  isolation (STORY-013), and the SPA (STORY-008+).

## Stopping conditions (BLOCKED protocol)

- An AC can't be met without changing the Architecture (name the section).
- A pre-existing test fails for reasons unrelated to your change.
- You'd need credentials, cloud resources or money.

## Final report shape

Per-AC ✅/❌ with file:line, the test counts (before 185 / after N), the `ruff check` result, the manual end-to-end
output (upload → review → PUT plan → cut → `done`, and the zip listing), the commits (on both remotes), and what
STORY-008 (the SPA) should know: every endpoint's status codes and body shapes, **cited as the tests that pin
them** (not hand-written JSON), the polling contract for `GET /api/jobs/{id}`, and the error `code` list.

## Orchestrator addendum (binding)

- **Plan validation must reject non-finite numbers**: NaN/±Infinity in any setting or override
  (`allow_inf_nan=False` / `math.isfinite`) → 422. `profile_from_dict`'s range checks let NaN through
  (STORY-001 review). Test it.
- **Page range**: every section `page` must be in `[1, pages]` → else 422 — the engine's `cuts.plan`
  raises IndexError past the book end (STORY-003 findings). Test page 0, pages+1.
- **Override cut coordinates** must be finite and within the page height; `startCol`/`endCol` ∈
  {full,left,right}. Test out-of-range values.
- The cut task's ZIP + per-section files must leave no partial `result.zip` on failure — test it
  non-vacuously (fail AFTER the file exists, prove the test fails without cleanup), like STORY-006.

## Previous attempt (RETRY — read this first)

Attempt 1 (`6fcf848`, docs `8f07e87`) passed everything except ONE confirmed finding — see
`docs/findings/STORY-007-review.md`. Fix forward, one commit on the feature/mvp tip:
`fix: STORY-007 - gate r1: previews never resurrect a deleted job`
1. The preview subprocess must never create the job directory: only create `png/<dpi>/` when
   `<job>/` still exists (e.g. `os.makedirs` of the png subtree guarded by `job_dir.is_dir()`, or
   write via a path whose parent must already exist) — if the job dir is gone, exit with a
   distinct "gone" result, write nothing.
2. `get_sheet` and `post_section_plan` re-check the row AFTER the subprocess returns: deleted/expired
   (or subprocess reported "gone") → 410 `expired`, and remove any PNG that raced in. A
   FileResponse on a file removed after the check must also map to 410, never 500.
3. Tests (non-vacuous — prove each fails on 6fcf848): a render that blocks until the test deletes
   the job (e.g. a hook/monkeypatched render or a slow fixture) → 410 and NO `<jobs>/<id>` dir
   afterwards; section-plan after DELETE → 410 not 500.
Update findings ("Gate r1 fix") and KICKOFF-STORY-008 if it cites these routes' error codes.

## Attempt 2b — round-2 fix (standing auto-fix policy) — READ THIS FIRST

One commit on the feature/mvp tip:
`fix: STORY-007 - gate r2: a sheet deleted after its render is 410, never 500`
In `get_sheet` (and check `post_section_plan` for the same ordering): read the rendered bytes FIRST,
then `settled(...)`; and whenever the bytes are missing (render "gone", file vanished, `_cached` None),
re-check the row — deleted/expired → 410, only a live job gets 500 `preview_failed`. Test: the
reviewer's deterministic settled-then-delete hook (scratchpad/rv007g2/fix/tests/test_zz_probe.py)
as a real test → 410; prove it fails on 3058d91. Fix the false sentence in the findings' "Gate r1 fix"
section ("the bytes were already read"). Keep all other tests green.
