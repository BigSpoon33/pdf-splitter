# Findings — STORY-005
**Date:** 2026-09-25
**Status:** done

## AC Verification
- [x] AC-1: `POST /api/jobs` (`src/pdf_splitter/upload.py:147` `create_job`, a sync route on the threadpool) mints the id first, creates `<jobs_dir>/<id>/` and copies the upload in 1 MiB chunks to `<id>/source.pdf.part` (`upload.py:98` `_copy_capped`). It gives up as soon as the running count passes `max_bytes` and returns 413 `too_large`. Contracts: `tests/test_upload.py::test_too_large`, `::test_too_large_stops_copying_at_the_cap` (the copy never reads more than one chunk past the cap), `::test_upload_exactly_at_the_cap_is_accepted`.
- [x] AC-2: The API checks `%PDF-` itself (`upload.py:122`) and then runs `[sys.executable, "-m", "pdf_splitter.preflight", "--max-pages", N, path]` under a 10 s timeout (`upload.py:71` `run_preflight`, `PREFLIGHT_TIMEOUT = 10.0`). `src/pdf_splitter/preflight.py:29` `check()` works in AC order: magic → `not_pdf`; open fails → `unreadable`; `needs_pass` → `encrypted`; `page_count > max_pages` → `too_many_pages` (413); fewer than 50 non-whitespace chars over up to 12 evenly spaced pages (`probe_indices`, first and last page included) → `no_text_layer`. A timeout, a non-zero exit, unparseable stdout, a missing/invalid `pages` or an unknown `code` all become 400 `unreadable`. Contracts: `::test_preflight_check` (6 cases), `::test_preflight_order_encrypted_before_page_count`, `::test_preflight_text_probe_needs_50_chars_across_sampled_pages`, `::test_probe_indices_are_evenly_spaced_and_capped`, `::test_preflight_subprocess_prints_one_json_object`, `::test_preflight_uses_a_10s_timeout_and_this_interpreter`, `::test_unreadable_on_preflight_timeout`, `::test_unreadable_on_preflight_crash_or_garbage` (7 cases).
- [x] AC-3: On success `source.pdf.part` → `source.pdf`, then `store.create_job(job_id=…, filename=sanitize_filename(...), bytes, pages, ttl_hours=settings.ttl_hours, ip_hash=…)` with the defaults `queued`/`analyze`, and a 201 `{id, state}` response (`upload.py:129-140`). `sanitize_filename` (`upload.py:51`) keeps the basename after `/` or `\`, drops every Unicode `C*` character (controls, and format chars such as U+202E), strips, falls back to `document.pdf`, and caps at 120 characters (keeping a short extension). Contracts: `::test_upload_creates_queued_analyze_job`, `::test_sanitize_filename` (9 cases), `::test_sanitize_filename_caps_at_120_keeping_the_extension`, `::test_uploaded_long_traversal_name_is_sanitized`.
- [x] AC-4: `_accept` (`upload.py:110`) runs everything after `mkdir` inside `try/finally`, and `rmtree`s `<id>/` unless the row was inserted. That covers every rejection, exceptions and timeouts, including a failing `create_job`. Every rejection test asserts no `<id>/` directory, no row (`COUNT(*) FROM jobs == 0`), and no `jobs_dir` path in the body (`assert_rejected`). Also `::test_row_insert_failure_leaves_no_directory`.
- [x] AC-5: The fixtures are generated in the test with PyMuPDF: a text PDF, an AES-256 encrypted one, an image-only one (a pixmap, no text), real PNG bytes uploaded as `x.pdf`, a 4-page PDF against `max_pages=3`, `%PDF-` + garbage, and an empty or missing file.
- Orchestrator addendum (job ids never reach logs): `cli.py:24` runs uvicorn with `access_log=False`. `src/pdf_splitter/access_log.py` is an HTTP middleware (registered in `app.py:35`) that logs `METHOD path status ms` on logger `pdf_splitter.access`. It replaces the segment after `/api/jobs/` with `log_id()` and drops the query string. Contracts: `::test_redact_path`, `::test_logs_never_contain_a_job_id`, and `tests/test_health.py::test_cli_api_serves_the_app_from_env_settings` (asserts `access_log is False`). Concurrency: `::test_concurrent_uploads_all_succeed` runs 6 threads × 3 uploads behind a `Barrier` with `raise_server_exceptions=True` and gets 18 distinct ids, rows and dirs.

## Test Results
**Command:** `uv run pytest && uv run ruff check`
**Result:** pass
```
67 passed in 4.79s
All checks passed!
```
Before: 23. After: 67 (44 in `tests/test_upload.py` counting parametrized cases; the existing CLI test gained an `access_log is False` assertion).

Manual run on port 8010 (8000 is held by an unrelated process), `PDFSPLIT_JOBS_DIR` pointed at a scratch dir, server stopped by PID:
```
curl -F "file=@book.pdf;filename=../../My Book.pdf"   → {"id":"eWyFfdQExP_jq1A246LCsQ","state":"queued"} 201
curl -F file=@blank.pdf                               → {"code":"no_text_layer","message":"The PDF has no text layer (it looks scanned); run OCR on it first."} 400
curl -F "file=@x.png;filename=x.pdf"                  → {"code":"not_pdf","message":"The file is not a PDF."} 400
(PDFSPLIT_MAX_BYTES=1000) curl -F file=@book.pdf      → {"code":"too_large","message":"The file is larger than the upload limit."} 413
jobs/ after: jobs.db + <id>/source.pdf only; row: queued|analyze|My Book.pdf|2500|5
server log: "POST /api/jobs 201 125.0ms", "GET /api/jobs/45ebecb8 404 0.3ms"; raw id occurrences in the log: 0
```

## Bugs Found
- **PyMuPDF 1.28.2 prints a `fitz` deprecation notice to STDOUT** (`warning: The \`fitz\` API is deprecated…`) when anything runs `import fitz`. That broke a "stdout is one JSON object" contract on the first run. Fixed two ways: `preflight.py` imports `pymupdf`, and `run_preflight` parses only the last stdout line. The kickoff's `import fitz` advice is outdated for this pin, and any subprocess protocol in STORY-006 should use the same last-line rule.

## Decisions (small ambiguities resolved)
- **Error body:** `{"code": str, "message": str}` (`upload.py:67` `reject`, messages in `upload.MESSAGES`). 413 for `too_large`/`too_many_pages`, and 400 for everything else. The body never includes a path or the id. `tests/test_upload.py::assert_rejected` is the contract.
- **`unreadable` is a new code.** Architecture § api/§ Interfaces list `not_pdf|encrypted|no_text_layer` for 400; the story adds `unreadable` (a crash or timeout). Architecture should add it (doc gap, not changed here).
- **Missing or empty file:** 400 `not_pdf` instead of FastAPI's 422 `{"detail"}`, so every upload rejection has the same shape.
- **Size cap timing:** FastAPI/Starlette parse the multipart body (spooling to a temp file after 1 MB) *before* the handler runs, so `too_large` is decided while copying from that spool, not while the bytes arrive. Memory use stays bounded (the story accepts this); the 210 MB network cap is Caddy's (STORY-013). A real early abort would need a raw `request.stream()` parser, which is only worth building if disk-spool abuse shows up.
- **`--max-pages` is passed to the preflight**, so a 50 000-page PDF is refused before the text probe.
- **Magic bytes are checked twice**: in the API (skips a spawn for obvious junk) and in `preflight.check` (so the module is safe on its own).
- **`ip_hash`** = `sha256(per-process random salt + request.client.host)` (`upload.py:47`). It stores no raw IP and ignores `X-Forwarded-For`. STORY-012 replaces it.
- **`app.py` → `deps.py`:** `get_settings`/`get_store`/`SettingsDep`/`StoreDep` moved to `src/pdf_splitter/deps.py`, because `upload.py` needs them and `app.py` imports `upload.py`. `app.py` re-exports them.
- **`job_dir.mkdir()` is outside the `try`**, so a (theoretical) id collision raises instead of letting the cleanup delete another job's directory.
- Preflight stderr is never logged, because MuPDF messages can quote the path (which contains the id). Only `log_id` + the exit code are logged.

## Handoff Context for Next Session
A successful upload leaves exactly `<jobs_dir>/<id>/source.pdf` and one row `state='queued', kind='analyze'` with `pages`/`bytes`/sanitized `filename`. `upload.run_preflight` is the pattern for the worker's subprocess runner: `sys.executable -m <module>`, `capture_output`, `stdin=DEVNULL`, `timeout`, the result on the LAST stdout line, anything else collapsed to one error code, and only `log_id` logged.

## Out-of-Scope Items
- Rate limiting, the trusted-proxy IP rule and the disk-full 503 (STORY-012). The early-abort streaming parser (see Decisions). Caddy's body cap (STORY-013).
- Doc gap: Architecture § api / § Interfaces don't list `unreadable` (400) as a `POST /api/jobs` code.
