# Findings — STORY-004
**Date:** 2026-09-25
**Status:** done

## AC Verification
- [x] AC-1: `uv run pdf-splitter api` serves `GET /api/health` → `{ok, queue, disk_free_gb, engine_version}`. The route is `src/pdf_splitter/app.py:50` (`create_app`), and the CLI is `src/pdf_splitter/cli.py:12` (`[project.scripts] pdf-splitter = "pdf_splitter.cli:main"`). `engine_version` = `monograph_splitter.__version__` ("0.4.1"). `JOBS_DIR` is created and the schema initialised in the lifespan (`app.py:37`). Contract: `tests/test_health.py` (3 tests).
- [x] AC-2: `Settings` (pydantic-settings, `env_prefix="PDFSPLIT_"`) is at `src/pdf_splitter/config.py:10`, with all nine fields. `MAX_BYTES` = 200 MiB = 209715200 bytes, and the README table says so. Contract: `tests/test_config.py` (defaults, prefix, unprefixed env ignored).
- [x] AC-3: `src/pdf_splitter/store.py`. The schema (`:11`) is Architecture § Storage Schema verbatim: `jobs` with nullable `owner`, the `jobs_queue` and `jobs_expiry` indexes, and `rate`. WAL is set in `connect()` (`:63`, together with `busy_timeout` 5 s and `isolation_level=None`). The store also has `create_job` (`:101`), `get_job` (`:131`), `claim_next` (`:138`, `BEGIN IMMEDIATE` + `UPDATE … WHERE id=(SELECT … ORDER BY created_at LIMIT 1) RETURNING *`), `update_progress` (`:162`), `set_state` (`:170`) and `expired` (`:188`). Contract: `tests/test_store.py::test_concurrent_claimers_never_share_a_job` (8 threads, each with its own `Store`, released by a `threading.Barrier` onto 20 queued jobs: all 20 were claimed exactly once).
- [x] AC-4: `new_job_id()` = `secrets.token_urlsafe(16)` (`store.py:47`), and `log_id()` = `sha256(id)[:8]` (`store.py:51`). All store logging goes through `log_id`. Contracts: `test_job_id_shape`, `test_log_id_is_short_sha256` and `test_logs_never_contain_the_raw_id`.
- [x] AC-5: 23 tests pass (20 from attempt 1 + 3 gate r1 regression tests), `ruff check` is clean, and the README has a Development section and the env var table.

## Test Results
**Command:** `uv run pytest && uv run ruff check && (uv run pdf-splitter api --port 8010 & sleep 3; curl -s localhost:8010/api/health)`
**Result:** pass
```
23 passed in 0.98s
All checks passed!
{"ok":true,"queue":0,"disk_free_gb":237.7,"engine_version":"0.4.1"}
```
(Attempt 1 ran the same line at 20 tests; the retry's `pytest` is the 23-test figure. The curl line is attempt 1's; the retry's server ran on 8011, see "Gate r1 fix".)
Baseline before: 0 tests (no code). I ran on port 8010 because an unrelated process (not started by this story) holds `127.0.0.1:8000` on this laptop, so the story's literal line gets `{"detail":"Not Found"}` from that other server. `uv run pdf-splitter api` itself defaults to 127.0.0.1:8000, and `test_cli_api_serves_the_app_from_env_settings` pins that default. The test server was stopped by PID.

## Bugs Found
- **Gate r1 (confirmed, fixed in `3da3255`):** `get_store` in `app.py` is a sync generator dependency. FastAPI opened the per-request `Store` in the endpoint's threadpool thread and ran the teardown (`store.close()`) as a separate threadpool call, often on another thread; with sqlite3's default `check_same_thread=True` the close raised `ProgrammingError` before `_conn` was dropped, so the connection (and its `jobs.db`/`-wal`/`-shm` fds) leaked and the ASGI exception dropped keep-alive clients (see `docs/findings/STORY-004-review.md`). Fix and evidence under "Gate r1 fix" below.

## Gate r1 fix
Commit `3da3255` (`fix: STORY-004 - gate r1: per-request store connections survive cross-thread teardown`), on top of `ffcf8a4`:
- `store.py:63` `Store.connect()` opens with `check_same_thread=False`. This is safe because each `Store` is used by ONE request (or one worker thread) at a time and never shared concurrently; the WHY comment says so. `BEGIN IMMEDIATE`, WAL and `busy_timeout` are unchanged.
- `store.py:84` `Store.close()` wraps the close in `try/finally` so `_conn` is always dropped, even if closing raises; a second `close()` is a no-op.
- `app.py:19` `get_store` keeps one fresh `Store` per request (the comment now states the real reason: requests on different threadpool threads must not share a connection, and the teardown may run on another thread).
- Regression tests, all three FAIL on `20e34fa` (verified by running them with `PYTHONPATH` pointed at that commit's `src/`) and pass now:
  - `tests/test_store.py::test_connection_survives_cross_thread_close`: opens/uses the connection in thread A, which stays alive (an `Event`) while thread B closes it. Keeping A alive matters: a finished thread's id gets reused by the next thread, which let a first draft of this test pass on the broken code by accident.
  - `tests/test_store.py::test_close_always_drops_the_connection`: a connection whose `close()` raises still leaves `_conn is None`.
  - `tests/test_health.py::test_concurrent_health_requests_all_succeed`: 16 threads × 25 `GET /api/health` through one `TestClient(raise_server_exceptions=True)`, released by a `Barrier`; asserts 0 server exceptions and 400 × 200. On `20e34fa` it fails with the same `ProgrammingError` the gate saw.
- Live repro (the orchestrator's `scratchpad/s004/burst.py` + `keepalive.py`) against `uv run pdf-splitter api --port 8011` (port 8000 is held by an unrelated process on this laptop), `PDFSPLIT_JOBS_DIR` pointed at a scratch dir:
  ```
  burst 600 @ c64:            {200: 600}
  burst 600 @ c64 (again):    {200: 600}
  keepalive 16 clients × 200: {200: 3200}     (no RemoteDisconnected, no server_said_close)
  ProgrammingError lines in the server log: 0   Tracebacks: 0
  open fds on jobs.db* in the server process after the run: 0
  ```
  Attempt 1's server at the same load produced 585–2060 tracebacks and ~450 leaked fds. The server was stopped by PID (`lsof -ti :8011` → `kill`).

## Decisions (small ambiguities resolved)
- **Engine pin:** `monograph-splitter @ git+https://github.com/BigSpoon33/pdf-splitter-engine@v0.4.1`, which is `uv.lock` `rev=v0.4.1`. The story's Depends-On text ("v0.3.1 until STORY-003 lands") is stale; its Implementation Notes already say v0.4.1. `pymupdf==1.28.2` is pinned explicitly. The first real install from the public repo worked anonymously.
- **`httpx2` instead of `httpx`** as the TestClient dev dep: starlette 1.7 warns that `httpx` with its TestClient is deprecated. `httpx2` silences the warning and works as a drop-in.
- **One connection per request** in the API (`app.py:get_store`, a FastAPI dependency), so concurrent requests on threadpool threads never share a connection. The connection is opened with `check_same_thread=False` (gate r1 fix) only because FastAPI may tear the dependency down on a different thread than the endpoint ran on; a `Store` must still never be used by two threads at once. The worker should likewise hold one `Store` per thread.
- **Timestamps** are `datetime.now(UTC).isoformat(timespec="seconds")` (`store.now_ts`), with non-UTC inputs normalised to UTC. Every mutating store method takes `now=` for tests.
- **`set_state` overwrites `error_code` and `message`** (to NULL when not passed), so moving a failed job back to `queued` clears its old error. `kind` is kept unless passed.
- `create_job`/`set_state`/`claim_next` reject unknown states/kinds with `ValueError` (states: queued|running|review|done|failed|deleted, kinds: analyze|cut).
- `Settings.public_url` defaults to `http://localhost:8000`, and `Settings.db_path` = `jobs_dir / "jobs.db"`.

## Handoff Context for Next Session
Tests build settings as `Settings(jobs_dir=tmp_path / "jobs")` (fixture `settings` in `tests/conftest.py`) and the app as `TestClient(create_app(settings))` inside a `with` block, so the lifespan creates `JOBS_DIR` and the schema. Routes get settings and the DB through `SettingsDep`/`StoreDep` (`app.py`). `create_job` is keyword-only and takes `job_id=`, so the upload can mint `new_job_id()` first, stream to `/jobs/<id>/source.pdf.part`, and only insert the row after preflight passes.

## Out-of-Scope Items
- `ip_hash` is NOT NULL, but the daily-rotating salt belongs to rate limiting (STORY-012). STORY-005 needs *some* hash to create rows (see KICKOFF-STORY-005).
- No doc errors found beyond those already listed in STORY-003's findings.
