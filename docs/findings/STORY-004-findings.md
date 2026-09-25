# Findings — STORY-004
**Date:** 2026-09-25
**Status:** done

## AC Verification
- [x] AC-1: `uv run pdf-splitter api` serves `GET /api/health` → `{ok, queue, disk_free_gb, engine_version}`. The route is `src/pdf_splitter/app.py:50` (`create_app`), and the CLI is `src/pdf_splitter/cli.py:12` (`[project.scripts] pdf-splitter = "pdf_splitter.cli:main"`). `engine_version` = `monograph_splitter.__version__` ("0.4.1"). `JOBS_DIR` is created and the schema initialised in the lifespan (`app.py:37`). Contract: `tests/test_health.py` (3 tests).
- [x] AC-2: `Settings` (pydantic-settings, `env_prefix="PDFSPLIT_"`) is at `src/pdf_splitter/config.py:10`, with all nine fields. `MAX_BYTES` = 200 MiB = 209715200 bytes, and the README table says so. Contract: `tests/test_config.py` (defaults, prefix, unprefixed env ignored).
- [x] AC-3: `src/pdf_splitter/store.py`. The schema (`:11`) is Architecture § Storage Schema verbatim: `jobs` with nullable `owner`, the `jobs_queue` and `jobs_expiry` indexes, and `rate`. WAL is set in `connect()` (`:63`, together with `busy_timeout` 5 s and `isolation_level=None`). The store also has `create_job` (`:90`), `get_job` (`:120`), `claim_next` (`:127`, `BEGIN IMMEDIATE` + `UPDATE … WHERE id=(SELECT … ORDER BY created_at LIMIT 1) RETURNING *`), `update_progress` (`:151`), `set_state` (`:159`) and `expired` (`:177`). Contract: `tests/test_store.py::test_concurrent_claimers_never_share_a_job` (8 threads, each with its own `Store`, released by a `threading.Barrier` onto 20 queued jobs: all 20 were claimed exactly once).
- [x] AC-4: `new_job_id()` = `secrets.token_urlsafe(16)` (`store.py:47`), and `log_id()` = `sha256(id)[:8]` (`store.py:51`). All store logging goes through `log_id`. Contracts: `test_job_id_shape`, `test_log_id_is_short_sha256` and `test_logs_never_contain_the_raw_id`.
- [x] AC-5: 20 tests pass, `ruff check` is clean, and the README has a Development section and the env var table.

## Test Results
**Command:** `uv run pytest && uv run ruff check && (uv run pdf-splitter api --port 8010 & sleep 3; curl -s localhost:8010/api/health)`
**Result:** pass
```
20 passed in 0.22s
All checks passed!
{"ok":true,"queue":0,"disk_free_gb":237.7,"engine_version":"0.4.1"}
```
Baseline before: 0 tests (no code). I ran on port 8010 because an unrelated process (not started by this story) holds `127.0.0.1:8000` on this laptop, so the story's literal line gets `{"detail":"Not Found"}` from that other server. `uv run pdf-splitter api` itself defaults to 127.0.0.1:8000, and `test_cli_api_serves_the_app_from_env_settings` pins that default. The test server was stopped by PID.

## Bugs Found
none

## Decisions (small ambiguities resolved)
- **Engine pin:** `monograph-splitter @ git+https://github.com/BigSpoon33/pdf-splitter-engine@v0.4.1`, which is `uv.lock` `rev=v0.4.1`. The story's Depends-On text ("v0.3.1 until STORY-003 lands") is stale; its Implementation Notes already say v0.4.1. `pymupdf==1.28.2` is pinned explicitly. The first real install from the public repo worked anonymously.
- **`httpx2` instead of `httpx`** as the TestClient dev dep: starlette 1.7 warns that `httpx` with its TestClient is deprecated. `httpx2` silences the warning and works as a drop-in.
- **One connection per request** in the API (`app.py:get_store`, a FastAPI dependency): sqlite3 connections are thread-bound, and sync endpoints run on the threadpool. The worker should likewise hold one `Store` per thread.
- **Timestamps** are `datetime.now(UTC).isoformat(timespec="seconds")` (`store.now_ts`), with non-UTC inputs normalised to UTC. Every mutating store method takes `now=` for tests.
- **`set_state` overwrites `error_code` and `message`** (to NULL when not passed), so moving a failed job back to `queued` clears its old error. `kind` is kept unless passed.
- `create_job`/`set_state`/`claim_next` reject unknown states/kinds with `ValueError` (states: queued|running|review|done|failed|deleted, kinds: analyze|cut).
- `Settings.public_url` defaults to `http://localhost:8000`, and `Settings.db_path` = `jobs_dir / "jobs.db"`.

## Handoff Context for Next Session
Tests build settings as `Settings(jobs_dir=tmp_path / "jobs")` (fixture `settings` in `tests/conftest.py`) and the app as `TestClient(create_app(settings))` inside a `with` block, so the lifespan creates `JOBS_DIR` and the schema. Routes get settings and the DB through `SettingsDep`/`StoreDep` (`app.py`). `create_job` is keyword-only and takes `job_id=`, so the upload can mint `new_job_id()` first, stream to `/jobs/<id>/source.pdf.part`, and only insert the row after preflight passes.

## Out-of-Scope Items
- `ip_hash` is NOT NULL, but the daily-rotating salt belongs to rate limiting (STORY-012). STORY-005 needs *some* hash to create rows (see KICKOFF-STORY-005).
- No doc errors found beyond those already listed in STORY-003's findings.
