# KICKOFF — STORY-005: Web: upload endpoint with preflight

## What you're walking into

"pdf-splitter" is a public site: someone drops in a big PDF and gets back one PDF per chapter or section.
The product lives in two repos:

- **Web (you write code here):** `~/Documents/Repos/pdf-splitter` (GitHub `BigSpoon33/pdf-splitter` = `origin`,
  Gitea mirror = `gitea`), branch **`feature/mvp`**. Stay on that branch; the loop keeps one branch per repo.
  STORY-004 made it a Python project: `20e34fa feat: STORY-004 - scaffold: FastAPI app, settings, SQLite job
  store, health`, its docs commit, the gate r1 fix `3da3255 fix: STORY-004 - gate r1: per-request store
  connections survive cross-thread teardown`, and that fix's docs commit. **Baseline: 23 tests
  pass (`uv run pytest`), and `uv run ruff check` is clean.** `docs/loop-state.json` belongs to the orchestrator,
  so never stage it.
- **Engine (read-only):** `~/Documents/Repos/monograph-splitter` (GitHub `BigSpoon33/pdf-splitter-engine`),
  pinned at tag `v0.4.1` in `pyproject.toml`/`uv.lock`. You don't call the engine in this story. Preflight uses
  PyMuPDF (`import fitz`, `pymupdf==1.28.2`, already a direct dependency) and nothing else.
- Read, in order: `docs/stories/STORY-005.md` (its ACs are authoritative), `docs/Architecture.md` § "api"
  (the failure codes), § "Interfaces" (`POST /api/jobs` responses, File layout), § ADR-005 (preflight runs in a
  subprocess with a 10 s timeout) and § ADR-007, then `docs/findings/STORY-004-findings.md`.

Toolchain: `uv 0.12.10`, Python 3.12 (`.python-version`). Dev deps: pytest, ruff and `httpx2` (the
TestClient transport; plain `httpx` triggers a starlette deprecation warning).

## What STORY-004 established (use these, don't re-invent)

- `src/pdf_splitter/config.py:Settings`: pydantic-settings, `PDFSPLIT_` prefix. The fields are `jobs_dir`
  (default `./jobs`), `max_bytes` (200 MiB = 209715200 bytes), `max_pages` 2000, `ttl_hours` 24, `workers`,
  `rate_per_hour`, `analyze_timeout`, `cut_timeout` and `public_url`. The property `db_path` =
  `jobs_dir / "jobs.db"`. Contract: `tests/test_config.py`.
- `src/pdf_splitter/app.py:create_app(settings=None)`: the factory. Settings live on `app.state.settings`.
  The lifespan creates `jobs_dir` and runs `Store.init()`. Routes take dependencies
  `SettingsDep = Annotated[Settings, Depends(get_settings)]` and `StoreDep = Annotated[Store, Depends(get_store)]`
  (**one fresh Store/connection per request**, so requests on different threadpool threads never share one; the connection is opened with `check_same_thread=False` because FastAPI may run the teardown on another thread, but a `Store` must never be used by two threads at once). Add your route
  inside `create_app` (or in `upload.py` as an `APIRouter` included there) using those deps. Never read env at
  import time. Contract: `tests/test_health.py`.
- `src/pdf_splitter/store.py`:
  - `new_job_id() -> str`: `secrets.token_urlsafe(16)`, 22 chars `[A-Za-z0-9_-]`.
  - `log_id(job_id) -> str`: `sha256(id)[:8]`. **Every log line about a job uses this and never the raw id.**
    Contract: `tests/test_store.py::test_logs_never_contain_the_raw_id`.
  - `now_ts(now=None) -> str`: UTC `isoformat(timespec="seconds")`.
  - `Store(path)`, with `.init()`, `.close()` and `.conn` (lazy; WAL, busy_timeout 5 s, `isolation_level=None`, so
    each statement autocommits unless you `BEGIN`).
  - **`Store.create_job(*, ip_hash: str, filename: str, bytes: int, pages: int, ttl_hours: int,
    kind: str = "analyze", state: str = "queued", job_id: str | None = None, now: datetime | None = None)
    -> dict`**. Every argument is keyword-only. Pass `job_id=` to use the id you already minted for the
    directory, and `ttl_hours=settings.ttl_hours`. Returns the full row as a dict. Raises `ValueError` on an
    unknown state or kind.
  - `get_job(id) -> dict | None`, `queue_length()`, `claim_next(kind)`, `update_progress(...)`,
    `set_state(...)` and `expired(now=None)`. Contract: `tests/test_store.py`.
- Test pattern (`tests/conftest.py`): the fixture `settings` = `Settings(jobs_dir=tmp_path / "jobs")`, and the
  fixture `store` = an initialised `Store(tmp_path / "jobs.db")`. Use the app as
  `with TestClient(create_app(settings)) as client:` (the `with` runs the lifespan). Tests must never touch
  the real `./jobs`.

## Critical gotchas

1. **Mint the id first, insert the row last.** Take `job_id = new_job_id()` and create
   `jobs_dir/<id>/source.pdf.part`. Stream the upload in chunks, counting bytes, and abort at `> max_bytes`
   with 413 `too_large`. Then run preflight, rename to `source.pdf`, and only then call
   `store.create_job(job_id=job_id, …)`. AC-4 requires that every rejection path removes the `.part` **and
   the `<id>/` directory** and leaves no row. Use a `try/finally`-style cleanup that also covers
   exceptions and timeouts.
2. **Don't buffer the whole file.** `UploadFile` spools to a temp file after 1 MB, which is acceptable
   per the story, but copy it with `await file.read(CHUNK)` in a loop and check the count as you go. A test
   with `Settings(jobs_dir=…, max_bytes=<small>)` makes the 413 cheap to trigger. Caddy's body cap is
   STORY-013.
3. **The preflight subprocess** is `python -m pdf_splitter.preflight <path>` and prints one JSON object.
   Launch it with `sys.executable`, not `"python"`, because the venv interpreter must be the one that runs it. Use
   `subprocess.run(..., timeout=10, capture_output=True)`, run it off the event loop (a sync route or
   `run_in_threadpool`), and treat any non-zero exit, unparseable stdout or `TimeoutExpired` as 400
   `unreadable`. Check the magic bytes (`%PDF-`) in the API before spawning. The order of checks
   is the AC-2 list. The text probe samples up to 12 evenly spaced pages and fails when the total is < 50 chars.
4. **Error body shape.** Architecture § api: every rejection is a specific 4xx with a `code`, and nothing may
   leak internal paths. Pick one JSON shape (for example `{"code": "...", "message": "..."}`), put it in the
   tests, and record it in findings. Later stories and the SPA (STORY-008) will key on `code`. Don't use the
   FastAPI default `{"detail": …}` for these.
5. **`ip_hash` is NOT NULL** and the salted, daily-rotating hash is STORY-012's job. For now hash the peer
   address (`request.client.host`) with sha256 plus a per-process random salt, in one small helper that
   STORY-012 will replace. Don't store the raw IP, and don't trust `X-Forwarded-For` yet (STORY-012 decides
   the trusted-proxy rule).
6. **Filename sanitising** (AC-3): take the display name only, basename-only (strip any `/` or `\` path), drop
   control characters, cap at 120 characters, and fall back to `document.pdf` when empty. The filename is
   never used as a path, because files are always `<id>/source.pdf`.
7. **Fixtures:** build the PDFs in the test with `fitz`. A text PDF is `doc.new_page(); page.insert_text(...)`
   with enough text (≥ 50 chars in total). The encrypted one is `doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256,
   user_pw="x", owner_pw="y")`. The image-only one inserts a pixmap/image and no text. The `.png` one is real PNG
   bytes uploaded as `x.pdf`. For too many pages, use `Settings(max_pages=3)` with a 4-page PDF. Test that a
   crash or timeout gives `unreadable` by monkeypatching the preflight timeout or runner, rather than hunting
   for a pathological PDF.
8. The preflight subprocess needs no network and writes nothing, but ADR-005's rlimits/sandbox belong to
   the worker story (STORY-006). Don't build the sandbox here, beyond the timeout.

## Recommended ordering

1. `src/pdf_splitter/preflight.py`: `main(argv)` → JSON `{ok, code?, pages?}` (whichever shape you
   choose; test it directly as a function **and** via the subprocess once). Cover the AC-2 checks there.
2. `src/pdf_splitter/upload.py`: the streaming copy with the size cap, `run_preflight(path, timeout)`,
   `sanitize_filename`, the ip-hash helper, and the `POST /api/jobs` handler (201 `{id, state}`).
3. Wire the route into `app.py:create_app`.
4. `tests/test_upload.py`: one test per rejection (`too_large`, `not_pdf`, `encrypted`, `too_many_pages`,
   `no_text_layer`, `unreadable`), each asserting the status, the `code`, **no leftover `<id>/` directory** and
   **no row** (`store.queue_length() == 0`, or a count over `jobs`). Add the happy path: 201, the id shape,
   `source.pdf` exists, and the row has `state='queued'`, `kind='analyze'`, `pages` and `bytes` right, and a sanitized
   `filename`. Add a sanitize test with a 300-char and a `../` name.
5. `uv run pytest && uv run ruff check`, plus a manual `curl -F file=@some.pdf localhost:<port>/api/jobs`. If
   `127.0.0.1:8000` is taken on this laptop (it was during STORY-004 by an unrelated process), use
   `pdf-splitter api --port 8010`, and kill only your own server by PID (`lsof -ti :8010`, then `kill <pid>`).

## Conventions

- Python 3.12, `from __future__ import annotations`, type hints, and small modules. Comments explain WHY, never
  WHAT. `ruff check` must be clean (`[tool.ruff] line-length = 110`).
- Commit on `feature/mvp`: `feat: STORY-005 - upload with streaming size cap and sandboxed preflight`, ending
  with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Stage explicit paths only (never
  `git add -A` / `.`, and never `docs/loop-state.json`). Never `reset --hard` / `checkout .`.
- Push: `git push origin feature/mvp` and `git push gitea feature/mvp`.
- Findings and the next kickoff go in `docs/findings/STORY-005-findings.md` and `docs/KICKOFF-STORY-006.md`,
  committed as `docs: STORY-005 - findings + KICKOFF-STORY-006`. Set STORY-005's status lines to Done.

## Authority

- Free: `src/pdf_splitter/` (new `upload.py`, `preflight.py`, and edits to `app.py`/`store.py` if needed, with
  existing tests kept green), `tests/`, `pyproject.toml`/`uv.lock` if a dependency is truly needed, `README.md`, and in `docs/`
  only `findings/`, `KICKOFF-*` and STORY-005's status lines.
- Do not touch: the engine repo, `~/Documents/AI/Inkwell`, `~/Documents/Vaults`, `docs/loop-state.json`, and
  PRD/Architecture (report doc errors in findings).
- Out of scope: rate limiting, the disk-full 503 and the trusted-proxy IP (all STORY-012), analysis/the worker
  (STORY-006), `GET /api/jobs/{id}` (STORY-007 unless its story says otherwise), the frontend and Docker.

## Stopping conditions (BLOCKED protocol)

- An AC can't be met without changing the Architecture (name the section).
- A pre-existing test fails for reasons unrelated to your change.
- You'd need credentials, cloud resources or money.

## Final report shape

Per-AC ✅/❌ with file:line, the test counts (before 23 / after N), the `ruff check` result, the manual curl
output, the commits (on both remotes), and what STORY-006 (worker + analyze) should know: the error-body
shape, where the source lands, the row a successful upload leaves, and how preflight is invoked (a reusable
subprocess-runner pattern for the worker). Cite the tests as the contract, not hand-written JSON.

## Orchestrator addendum (after STORY-004's gate)

- **Job ids must not reach logs** (ADR-007: the id is the only credential). Once you add
  `/api/jobs…` routes, uvicorn's default access log would print raw ids. Run uvicorn with
  `access_log=False` in `cli.py` and add a small request-logging middleware (or log filter) that logs
  method, status, duration and the path with any job id replaced by `log_id(id)`. Test it.
- The store fix from STORY-004's retry (`check_same_thread=False`, always-drop-on-close) landed as
  `3da3255`; keep using `StoreDep`. Its contracts: `tests/test_store.py::test_connection_survives_cross_thread_close`,
  `::test_close_always_drops_the_connection` and `tests/test_health.py::test_concurrent_health_requests_all_succeed`
  (23 tests now). Your upload route should be exercised by a similar concurrent-requests test.
