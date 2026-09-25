# KICKOFF — STORY-006: Web: worker runner, sandbox, analyze task

## What you're walking into

"pdf-splitter" is a public site: someone drops in a big PDF and gets back one PDF per chapter or section.
The product lives in two repos:

- **Web (you write code here):** `~/Documents/Repos/pdf-splitter` (GitHub `BigSpoon33/pdf-splitter` = `origin`,
  Gitea mirror = `gitea`), branch **`feature/mvp`**. Stay on that branch. STORY-005 landed as
  `b84c3c4 feat: STORY-005 - upload with streaming size cap and sandboxed preflight`, its docs commit, the gate
  r1 fix `4006eff fix: STORY-005 - gate r1: ids redacted in any path shape, log paths escaped, preflight argv
  hardened`, the gate r2 fix `676dbbf fix: STORY-005 - gate r2: redact any long id-alphabet run, not only exact
  22-char tokens`, and each fix's docs commit. **Baseline: 118 tests pass (`uv run pytest`), and `uv run ruff
  check` is clean.** `docs/loop-state.json` belongs to the orchestrator, so never stage it.
- **Engine (read-only here):** `~/Documents/Repos/monograph-splitter` (GitHub `BigSpoon33/pdf-splitter-engine`),
  pinned at tag `v0.4.1` in `pyproject.toml`/`uv.lock`. The surface you need:
  - `monograph_splitter.detect`: `outline_levels(doc) -> [{level, count}]`, `outline_entries(doc, level)`, and
    `heading_candidates(doc, *, min_ratio=1.3, max_len=90, profile=…)` → `{body_size, levels, candidates}`.
  - `monograph_splitter.profile`: `WEB_BASE` and `profile_from_dict(d, base=WEB_BASE)`.
  - `monograph_splitter.index`: `index_book(book, cache, prof, log=print)` has **no per-page progress hook in
    v0.4.1** (it is `[index_page(page, prof) for page in book]`), and `index_page(page, prof)` is public. The story
    says "prefer the engine hook". Adding one is an engine change (tests + diff gate + a new tag + a pin bump), so
    either do that as a clearly separate engine commit/tag, or call `index_page` per page in the task and write the
    same cache shape. Record the choice in findings. The engine's synthetic book builders are in its
    `tests/fixtures.py` (`heading_book`, `headed_book(path, outline=True)`, `single_column_book`, …): port what
    you need into `tests/fixtures/` here, and don't import the engine's tests.
- Read, in order: `docs/stories/STORY-006.md` (its ACs are authoritative), `docs/Architecture.md` § worker,
  § Data Types (the Analysis and Plan shapes), § File layout, § Job states, § ADR-005, then
  `docs/findings/STORY-005-findings.md`.

Toolchain: `uv 0.12.10`, Python 3.12. Dev deps: pytest, ruff and `httpx2`.

## What STORY-005 established (use these, don't re-invent)

- `src/pdf_splitter/upload.py`:
  - `run_preflight(path, max_pages, job_id, timeout=None)`: **the subprocess-runner pattern to copy**:
    `[sys.executable, "-m", "<module>", <options>, "--", <positional>]`, `capture_output=True`, `stdin=DEVNULL`,
    `timeout=`, and the result read from the **last stdout line** only. `TimeoutExpired`, a non-zero exit or
    unparseable output collapse to one error code. Only `log_id(job_id)` and the exit code are logged, never
    stderr, because it can contain the path, and the path contains the id. **Always put `--` before a
    positional that can start with `-`**: 1 in 64 job ids does (`token_urlsafe`), so `python -m pdf_splitter.task
    analyze -bCd…` without `--` is an argparse error → a spurious failure. Contract:
    `tests/test_upload.py::test_preflight_uses_a_10s_timeout_and_this_interpreter`,
    `::test_upload_with_relative_jobs_dir_and_dash_id`.
  - `Settings.jobs_dir` is **always absolute** (an after-validator `resolve()`s it, `config.py`), so is `db_path`;
    `Settings(jobs_dir=Path("."))` is `Path.cwd()`. Contract: `tests/test_config.py::test_jobs_dir_is_always_absolute`.
  - A successful upload leaves exactly `<jobs_dir>/<id>/source.pdf` and one row `state='queued'`,
    `kind='analyze'`, with `pages`, `bytes`, a sanitized `filename` and `expires_at = created + ttl_hours`.
    Contract: `tests/test_upload.py::test_upload_creates_queued_analyze_job`.
  - Error body for API rejections: `{"code", "message"}` via `upload.reject(code)`. Contract:
    `tests/test_upload.py::assert_rejected`. (Worker failures go in the row's `error_code`/`message`, not HTTP.)
- `src/pdf_splitter/preflight.py`: an example of a `python -m pdf_splitter.<module>` entry with `main(argv)` that
  prints one JSON object, and `check()` testable in-process.
- `src/pdf_splitter/deps.py`: `get_settings`, `get_store`, `SettingsDep` and `StoreDep` (moved from `app.py`, which
  re-exports them).
- `src/pdf_splitter/access_log.py`: the request log with job ids hashed. `redact_path(s)` applies two rules
  (gate r2, Shuma-approved): (1) after a case-insensitive `/api/jobs/` with any repeated slashes, the whole next
  `[^/]+` segment becomes `log_id(segment)` whatever its length; then (2) **every maximal run of
  `[A-Za-z0-9_-]` that is 16+ chars long, anywhere in the string, becomes `log_id(run)`** (a whole run, never a
  slice), so `<id>x`, `x<id>`, `<id><id>`, `<id>-extra`, `<id>A` and truncations down to 16 chars are all hashed.
  Rule 2 is what makes it safe on a command line or a stderr tail, not just a path; rule 1 only fires on
  `/api/jobs/` paths. `loggable_path(s)` percent-encodes the redacted path so ESC/U+2028/`%` can't reach a
  terminal raw. **The worker reuses this rule**: pass any string that could carry an id (argv, a path, a stderr
  tail) through `redact_path` before logging it. Route words shorter than 16 (`sheets`, `sections`, `result.zip`,
  `plan`, `cut`) stay readable. `cli.py` runs uvicorn with `access_log=False`. Contracts:
  `tests/test_upload.py::test_redact_path`, `::test_redact_path_hashes_an_id_glued_to_other_alphabet_chars`,
  `::test_redact_path_cannot_be_dodged_by_path_shape` (exact expected strings; note `/api/jobs/<id>.json` logs
  `log_id("<id>.json")`), `::test_logged_path_is_escaped`, `::test_logs_never_contain_a_job_id`.
- From STORY-004: `Settings` (`workers`, `analyze_timeout`, `cut_timeout`, `jobs_dir`, `db_path`),
  `Store.claim_next(kind)` (atomic, `BEGIN IMMEDIATE`), `update_progress(id, progress, total, message)`,
  `set_state(id, state, kind=, error_code=, message=)` (overwrites error_code/message), `get_job`, and
  `log_id`. **One `Store` per thread.** A Store must never be used by two threads at once. Contracts:
  `tests/test_store.py`.
- Test pattern (`tests/conftest.py`): the `settings` fixture = `Settings(jobs_dir=tmp_path / "jobs")`, and the
  `store` fixture = an initialised `Store(tmp_path / "jobs.db")`.

## Critical gotchas

1. **PyMuPDF 1.28.2 prints a `fitz` deprecation warning to STDOUT** on `import fitz`. Use `import pymupdf` in
   every subprocess module, and read results from the last stdout line (see `run_preflight`). The engine
   itself does `import fitz` lazily (`detect.py:103`, `render.py`, `verify.py`), so the warning WILL land on the
   task's stdout mid-run: keep the result on the last line (or in a file), never "all of stdout".
2. **Job ids never reach logs** (ADR-007). The worker logs `log_id(id)` only. The task subprocess's argv
   contains the id (`python -m pdf_splitter.task analyze -- <id>`), so never log the command line or the stderr
   tail verbatim without passing it through `access_log.redact_path` first (its run rule hashes every 16+ run of
   the id alphabet anywhere in a string, so a glued or truncated id is caught too). Add a test like
   `test_logs_never_contain_a_job_id` for the worker, and assert no 16-char window of the id survives, the way
   `tests/test_upload.py::assert_id_gone` does.
   **The task's positional id needs `--` before it** (see `run_preflight` above); test it with a forced
   dash-leading id, the way `test_upload_with_relative_jobs_dir_and_dash_id` does.
3. **rlimits in `preexec_fn`**: `resource.setrlimit(RLIMIT_AS, 2 GB)`, `RLIMIT_CPU` (timeout + 10), and
   `RLIMIT_FSIZE` (1 GB). `preexec_fn` is not thread-safe with threads in the parent. If you run `WORKERS`
   concurrent jobs from threads, consider wrapping the child in a tiny launcher module that sets its own rlimits
   (`python -m pdf_splitter.worker.sandbox …`), or use `process_group`/`start_new_session` and document why.
   Distinguishing "resources" from "timeout" (AC-4): a wall `TimeoutExpired` → `timeout`, while death by
   SIGXCPU/SIGKILL, a `MemoryError` or an `RLIMIT_AS` failure → `resources`. Pin the mapping in a test with an
   injected task. **Addendum from STORY-005's review:** the preflight subprocess has a timeout but no memory
   rlimit, and a 2.5 KB nested-XObject text PDF reached ~790 MB RSS inside the 10 s window. Once your sandbox
   launcher exists, apply the same rlimits to `upload.run_preflight`'s launch (keep its shape and its tests
   green; a `test_preflight_runs_under_rlimits`-style check is enough) and record it in findings.
4. **Progress at most every 0.5 s** (AC-3): throttle in the task, and write with its own `Store`. The task is a
   separate process, so it opens its own connection to `settings.db_path`.
5. **Re-queue once (AC-5)**: there's no attempts column in the schema. Architecture § Storage Schema is the
   contract, so don't add columns without a findings note. One option: `message`/`error_code` as a marker, or
   `started_at` compared with the timeout. Choose, test it, and record it.
6. `analysis.json` / `plan.json` shapes are Architecture § Data Types. Pages are 1-based sheet numbers
   (ADR-003). The default plan's `settings` must round-trip through `profile_from_dict` (only `WEB_KEYS`).
7. Tests must never touch the real `./jobs`, and must not need the network (the worker container has none).

## Recommended ordering

1. `src/pdf_splitter/worker/analyze.py`: pure functions `analyze(pdf_path, work_dir, progress) -> dict`
   and `suggest(analysis)`, plus `default_plan(analysis)`. Test them in-process against ported synthetic books
   (AC-2, AC-6).
2. `src/pdf_splitter/worker/task.py`: `python -m pdf_splitter.task analyze <id>` (a shim at
   `src/pdf_splitter/task.py`, or point the command at `pdf_splitter.worker.task`, and record which). It
   resolves `<jobs_dir>/<id>`, runs analyze with throttled `update_progress` (AC-3), writes `analysis.json` +
   `plan.json`, then `set_state(id, "review")`.
3. `src/pdf_splitter/worker/runner.py`: the claim loop, ≤ `WORKERS` concurrent, the subprocess launch with
   rlimits + wall timeout, and the failure mapping (`timeout`|`resources`|`internal`) (AC-1, AC-4). Add a
   startup re-queue sweep (AC-5). Make the task command injectable so the tests can run a slow/crashing/
   memory-hog fake.
4. `cli.py`: the `pdf-splitter worker` subcommand.
5. `tests/test_worker.py`: one test per AC, including "the loop keeps running after a failed job".
6. `uv run pytest && uv run ruff check`, then a manual end-to-end: `pdf-splitter api --port 8010` +
   `pdf-splitter worker` against one scratch `PDFSPLIT_JOBS_DIR`, upload with curl, and watch the row reach
   `review`. Port 8000 is taken by an unrelated process on this laptop. Kill only your own servers, by PID
   (`lsof -ti :8010`, then `kill <pid>`), and never use `pkill -f`.

## Conventions

- Python 3.12, `from __future__ import annotations`, type hints, and small modules. Comments explain WHY, never
  WHAT. `ruff check` must be clean (line-length 110).
- Commit on `feature/mvp`: `feat: STORY-006 - sandboxed worker runner and analyze task (outline + heading
  candidates)`, ending with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Stage explicit
  paths only (never `git add -A` / `.`, and never `docs/loop-state.json`). Never `reset --hard` / `checkout .`.
- Push: `git push origin feature/mvp` and `git push gitea feature/mvp`.
- Findings and the next kickoff go in `docs/findings/STORY-006-findings.md` and `docs/KICKOFF-STORY-007.md`,
  committed as `docs: STORY-006 - findings + KICKOFF-STORY-007`. Set STORY-006's status lines to Done.

## Authority

- Free: `src/pdf_splitter/` (new `worker/`, the task entry, and `cli.py`; edits to `store.py` keep existing
  tests green), `tests/`, `pyproject.toml`/`uv.lock` if a dependency is truly needed, `README.md`, and in `docs/`
  only `findings/`, `KICKOFF-*` and STORY-006's status lines.
- The engine repo: only if you choose to add the progress hook (a separate engine commit + tag + pin bump, with
  the engine's own tests and diff gate green). Otherwise read-only.
- Do not touch: `~/Documents/AI/Inkwell`, `~/Documents/Vaults`, `docs/loop-state.json`, and PRD/Architecture
  (report doc errors in findings).
- Out of scope: the cut task and the job GET/plan endpoints (STORY-007+), network isolation/compose (STORY-013),
  rate limiting (STORY-012), and the frontend.

## Stopping conditions (BLOCKED protocol)

- An AC can't be met without changing the Architecture (name the section).
- A pre-existing test fails for reasons unrelated to your change.
- You'd need credentials, cloud resources or money.

## Final report shape

Per-AC ✅/❌ with file:line, the test counts (before 118 / after N), the `ruff check` result, the manual end-to-end
output (the row reaching `review`, and the `analysis.json` keys), the commits (on both remotes), and what STORY-007
should know: the runner API for adding the `cut` kind, the `plan.json` it will read, and the failure-code mapping.
Cite the tests as the contract, not hand-written JSON.

## Previous attempt (RETRY — read this first)

Attempt 1 (`12b4e9b`, docs `9dce66e`) passed everything except 2 CONFIRMED findings — see
`docs/findings/STORY-006-review.md`. Fix forward, one commit on the feature/mvp tip:
`fix: STORY-006 - gate r1: MuPDF allocation failures are resources; analysis JSON survives bad text`
1. `guarded`: also map MuPDF allocation failures to `resources` — a `RuntimeError` whose message
   matches MuPDF's allocator failure (`code=2` / `calloc|malloc|realloc … failed` / "out of memory"),
   keep everything else `internal`. Test with the reviewer's real bomb
   (scratchpad/rv006/mkbombs.py → xbomb6.pdf, copy the builder into a test fixture) under the real
   sandbox + Runner → row `failed/resources`; plus a unit case for the message match.
2. Make every string that reaches `analysis.json`/`plan.json` JSON-safe: replace lone surrogates
   (e.g. `s.encode("utf-8", "replace").decode()` or `errors="surrogatepass"`→replace) at the point
   titles/headings/labels/filenames enter the dict, so plan section names are clean too; write the
   file so it can't fail on encoding; clean up the `.tmp` on any write failure. Tests: the two
   bookmark byte sequences from the review → row reaches `review`, analysis.json parses, no `.tmp`.
Update findings ("Gate r1 fixes") and KICKOFF-STORY-007 (names from analysis are already clean).

## Attempt 3 (Shuma approved, 2026-09-25) — READ THIS FIRST

Round 2 (on e15e7f0) confirmed 2 findings — `docs/findings/STORY-006-review.md` § Round 2. One
commit on the feature/mvp tip:
`fix: STORY-006 - gate r2: MuPDF allocator failures are resources whatever the exception type; a real mid-write failure test`
1. `guarded`: classify by MESSAGE, not type — any exception that is a `RuntimeError` OR a
   `pymupdf.mupdf.FzErrorBase` subclass (import lazily/defensively) whose `str()` matches
   `MUPDF_ALLOC_FAILED` → `resources`; everything else stays `internal`. Fix the false comment
   ("raised as a plain RuntimeError"). Also make `analyze.page_labels()`' fallback catch `FzErrorBase`
   (a label failure must never fail the job). Tests: unit — `FzErrorSystem("code=2: malloc (65537 bytes) failed")`
   → resources, a non-alloc `FzErrorFormat` → internal; real — the reviewer's
   `scratchpad/rv006r2/mk_raw.py` link_uri_40k builder as a fixture (via `hostile.build`) under the real
   sandbox + `Runner.run_once` → `failed/resources` (mark slow if needed, but it must run in the suite).
2. Replace the vacuous `.tmp` test with one that fails AFTER the `.tmp` exists — e.g. a subprocess with
   `RLIMIT_FSIZE` small enough that `write_bytes` raises EFBIG mid-write, or monkeypatch `os.replace`
   to raise — asserting the previous file is intact and no `.tmp` remains. Prove it: it must FAIL if
   the try/except/unlink is removed (state that you checked this in findings).
Update findings ("Gate r2 fixes") and KICKOFF-STORY-007 (the cut task inherits the message-based rule).
