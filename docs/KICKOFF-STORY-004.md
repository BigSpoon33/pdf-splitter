# KICKOFF — STORY-004: Web: repo scaffold, settings, SQLite store, health

## What you're walking into

Two repos make up one product, "pdf-splitter": a public site where someone drops in a big PDF and gets back one
PDF per chapter or section.

- **Web (you write code here)**: `~/Documents/Repos/pdf-splitter` (GitHub `BigSpoon33/pdf-splitter` =
  `origin`, Gitea mirror = `gitea`), branch **`feature/mvp`**. So far it holds only docs: `README.md` (3 lines)
  and `docs/` (PRD, Architecture, stories, findings, kickoffs, and `docs/loop-state.json`, which belongs
  to the orchestrator, so never stage it). The tip is the latest STORY-003 docs commit
  (`docs: STORY-003 - findings + KICKOFF-STORY-004`, the retry's; the earlier one with the same subject
  is `639fd22`). **There is no `pyproject.toml`, no `src/`, no
  tests, so there's no test baseline: 0 tests.** STORY-004 is the first code in this repo. Stay on
  `feature/mvp`. The loop keeps one branch per repo, even though other stories' files say otherwise.
- **Engine (read-only for you)**: `~/Documents/Repos/monograph-splitter` (GitHub
  `BigSpoon33/pdf-splitter-engine`), branch `feature/web-mode`, tip **`8e52cc3`** (STORY-003 + its gate r1
  fix), with annotated tag **`v0.4.1`** pushed to GitHub and Gitea. `v0.4.0` (on `c8935d2`) also exists but is
  superseded: its review server turned a malformed `overrides.json` into a 404. `main` is still `6fd22fc`
  (0.3.1). Pin `v0.4.1`, not `v0.4.0` and not `main`. Engine tests: 150 passed.
- Read, in order: `docs/stories/STORY-004.md` (its ACs are authoritative), `docs/Architecture.md` §
  "store", § "Storage Schema", § "Interfaces" (`GET /api/health`, job id rules), § ADR-001/004/007,
  then `docs/findings/STORY-003-findings.md` (Handoff section).

Toolchain on this laptop: `uv 0.12.10`, CPython 3.12.12 at `/usr/bin/python3.12` (3.14 is the system
default, so pin 3.12 explicitly with `requires-python = ">=3.12"` plus a `.python-version` of `3.12`).
`ruff` is not installed globally; add it as a dev dependency.

## What STORY-003 established (use these, don't re-invent)

- The engine dependency line:
  `monograph-splitter @ git+https://github.com/BigSpoon33/pdf-splitter-engine@v0.4.1`.
  The same line with `@v0.4.0` was verified anonymously (`uv run --no-project --with "<that>" python -c
  "import monograph_splitter"` resolved and imported it, PyMuPDF 1.28.2 included); `v0.4.1` is one commit
  later on the same public repo and was seen with `git ls-remote`, but your `uv sync` is its first real
  install. The import name is `monograph_splitter`.
- **`engine_version` for `/api/health` = `monograph_splitter.__version__`** (`"0.4.1"`, new in
  `src/monograph_splitter/__init__.py:16`). Do NOT use `ENGINE_VERSION` (an int, 17): that's the index-cache
  key, not a release. `import monograph_splitter` alone doesn't import `fitz`, so health stays cheap.
  The contract is `tests/test_web_mode.py::test_the_package_version_is_the_pyproject_version` in the engine repo.
- The web engine surface that later stories use (not this one): `profile.profile_from_dict`,
  `detect.outline_levels/outline_entries/heading_candidates(doc, profile=prof)`,
  `session.Book.open(entries=rows)`, `session.Book.cut_all(progress=…)`. Their contracts are the
  engine tests `tests/test_profile_dict.py`, `tests/test_detect.py` and `tests/test_web_mode.py`. You
  don't call any of them in STORY-004.
- What `docs/Architecture.md` now says about that surface (§ "Engine additions" and § "Data Types",
  updated after STORY-003's gate), so nothing you build in the store or settings contradicts it:
  - `Book.cut_all(progress=None, verify=True, preview=False, only=None, *, limit=None, redact=True) -> dict`
    with keys `written, flags, notes, leaks, missing, unknown` (pinned by `tests/test_web_mode.py`
    `SUMMARY_KEYS`); `progress(done, total, name)` after each entry; one `Book` per cut job.
  - **Engine names vs display names.** `sections[].name` in a Plan is the DISPLAY name (≤ 120 chars,
    duplicates allowed). The engine never sees it: the worker passes a unique, filename-safe
    `NNN-<ascii-slug>` (≤ 80 bytes) per section, keeps the display name in the Plan, and translates
    `overrides` (keyed by section INDEX in the Plan) to engine names. ZIP entries are
    `NNN - <display name, sanitized>.pdf`. The engine refuses names containing `/`, `\`, NUL or equal
    to `.`/`..`/empty with a `ValueError` before any write.
  - **Page validation is the web layer's job.** Plan validation rejects `page ∉ [1, pages]` (the
    engine's `cuts.plan` raises `IndexError` past the book end). That is STORY-007's `PUT plan` → 422; in
    STORY-004 it only means the job row's `pages` column must be there for it (it is in the schema).
  - None of this changes STORY-004's schema or settings; it is here so the Store's `progress`/`total`/
    `message` columns are understood as the `cut_all` progress callback's `(done, total, name)`.

## Critical gotchas

1. **The dep pin.** The story's Depends-On says "pinned to v0.3.1 until STORY-003 lands" and its
   Implementation Notes say `@v0.4.0`; both are stale. STORY-003 has landed **and** been patched, so pin
   `@v0.4.1` (the story file is not yours to edit; record the pin in findings). Architecture's Dependency
   Map says to pin PyMuPDF exactly: add `pymupdf==1.28.2`
   explicitly (the engine only says `>=1.24`).
2. **Job ids** are `secrets.token_urlsafe(16)`: 16 random bytes, url-safe base64, 22 characters, no
   padding. Architecture § Interfaces says ids are "the only credential" and never appear in logs.
   Log `hashlib.sha256(id.encode()).hexdigest()[:8]` (write one helper and use it everywhere, and
   test that a log line never contains the raw id).
3. **`claim_next(kind)` atomicity.** Open every connection with `isolation_level=None`, which gives
   you explicit transaction control, and a `timeout`/`busy_timeout` of a few seconds. Then run
   `BEGIN IMMEDIATE; UPDATE jobs SET state='running', started_at=?, updated_at=? WHERE id = (SELECT id FROM jobs
   WHERE state='queued' AND kind=? ORDER BY created_at LIMIT 1) RETURNING *; COMMIT`. `RETURNING`
   needs SQLite ≥ 3.35, which Python 3.12's bundled library has. A `sqlite3.Connection` can't be shared
   across threads by default, so the concurrency test must give **each thread its own connection** (or its own `Store`),
   start them together with a `threading.Barrier`, then assert that the claimed ids are unique and their count is
   ≤ the number of queued jobs. `PRAGMA journal_mode=WAL` returns `'wal'`: assert it.
4. **Schema columns are NOT NULL**: `ip_hash`, `filename`, `bytes`, `pages`, `state`, `kind`, and the
   timestamps. `create_job` therefore needs them all as arguments (STORY-005 will pass the real values).
   Take the schema from Architecture § Storage Schema, including the `rate` table and both indexes,
   and keep `owner` nullable (ADR-007).
5. **Timestamps are TEXT and get compared** (`expired()` = `expires_at < now`, and `claim_next` orders by
   `created_at`). Use one fixed format everywhere, for example `datetime.now(UTC).isoformat(timespec="seconds")`,
   so lexicographic order is time order. Take a `now` parameter (or an injectable clock) so tests can
   make a job expire without sleeping. `expires_at = created_at + TTL_HOURS`.
6. **Settings** (pydantic-settings, `env_prefix="PDFSPLIT_"`): `JOBS_DIR`, `MAX_BYTES` (200 MB = 200 * 1024 * 1024,
   and say which unit in the README), `MAX_PAGES` 2000, `TTL_HOURS` 24, `WORKERS` 2, `RATE_PER_HOUR` 6,
   `ANALYZE_TIMEOUT` 300, `CUT_TIMEOUT` 600, `PUBLIC_URL`. Default `JOBS_DIR` to something dev-local
   (e.g. `./jobs`, gitignored). Tests must never touch it: build `Settings(jobs_dir=tmp_path)` or
   use env + `tmp_path`, and inject settings into the app with a factory (`create_app(settings)`),
   not a module-level global that reads the environment at import time.
7. **Health**: `{ok, queue, disk_free_gb, engine_version}`. `queue` = the count of `state='queued'`,
   and `disk_free_gb` = `shutil.disk_usage(JOBS_DIR).free / 1e9`, rounded. Create `JOBS_DIR` on
   startup if it's missing. Test it with FastAPI's `TestClient` (httpx is a dev dep).
8. **`uv run pdf-splitter api`**: a `[project.scripts] pdf-splitter = "pdf_splitter.cli:main"` (or
   `app:main`) with subcommands. Only `api` exists now; STORY-006 adds `worker`. The command runs
   uvicorn on `127.0.0.1:8000` by default (host/port flags are fine). The story's verification step
   backgrounds it and curls `/api/health`: kill that server **by PID** (`lsof -ti :8000`, then `kill <pid>`),
   never with `pkill -f`.
9. `README.md` already exists (3 lines, links to the planning docs). **Edit it** (add dev commands), don't
   recreate it or drop the links. Add a `.gitignore` (`.venv/`, `__pycache__/`, `jobs/`, `*.db*`).

## Recommended ordering

1. `pyproject.toml` (hatchling, `packages = ["src/pdf_splitter"]`, Python ≥ 3.12, deps: fastapi, uvicorn,
   pydantic-settings, python-multipart (STORY-005 needs it; fine to add now), `monograph-splitter @ git+…@v0.4.1`,
   `pymupdf==1.28.2`; dev group: pytest, httpx, ruff), `.python-version`, `.gitignore`, `uv sync`.
2. `src/pdf_splitter/config.py`: the `Settings` class (AC-2), plus `tests/test_config.py` for the env
   prefix and each default.
3. `src/pdf_splitter/store.py`: `Store(path)` with `connect()` (WAL, busy timeout), `init()` (schema),
   `new_job_id()`, `log_id(id)`, `create_job(...)`, `get_job(id)`, `claim_next(kind)`,
   `update_progress(id, progress, total, message=None)`, `set_state(id, state, *, kind=None,
   error_code=None, message=None)`, `expired(now=None)` (AC-3/4), and `tests/test_store.py`
   (the threaded claim test, the id shape `re.fullmatch(r"[A-Za-z0-9_-]{22}", id)`, the log hash, WAL, and expiry).
4. `src/pdf_splitter/app.py`: `create_app(settings)` with `GET /api/health`, and `tests/test_health.py` (AC-1).
5. The CLI entry `pdf-splitter api`, then the README dev commands (`uv sync`, `uv run pytest`, `uv run ruff check`,
   `uv run pdf-splitter api`, and the env vars table) (AC-5). Finally run the story's verification line.

## Conventions

- Python 3.12, `from __future__ import annotations`, type hints, and small modules. Comments explain WHY, never WHAT.
  `ruff check` must be clean (keep the default rule set, or add a modest `[tool.ruff]` with `line-length = 110`).
- Never use npm/yarn/pnpm/npx (none is needed here; Bun comes with the SPA in STORY-008).
- Commit on `feature/mvp`: `feat: STORY-004 - scaffold: FastAPI app, settings, SQLite job store, health`,
  ending with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Stage explicit paths only
  (never `git add -A` / `.`; never stage `docs/loop-state.json`), and include `uv.lock`. Never `reset --hard` / `checkout .`.
- Push: `git push origin feature/mvp` and `git push gitea feature/mvp`.
- Findings and KICKOFF-STORY-005 go in `docs/findings/STORY-004-findings.md` and
  `docs/KICKOFF-STORY-005.md` in this same repo, committed as `docs: STORY-004 - findings + KICKOFF-STORY-005`.

## Authority

- Free: everything new under `src/pdf_splitter/`, `tests/`, `pyproject.toml`, `uv.lock`, `.python-version`,
  `.gitignore`, `README.md`, and in `docs/` only `findings/`, `KICKOFF-*` and STORY-004's status lines.
- Do not touch: the engine repo (report engine needs in findings; they become an engine story),
  `~/Documents/AI/Inkwell` and `~/Documents/Vaults`, `docs/loop-state.json`, and the PRD/Architecture
  (report doc errors in findings; STORY-003's findings already list some).
- Out of scope: upload, preflight, worker, janitor, rate limiting, frontend, Docker.

## Stopping conditions (BLOCKED protocol)

- The engine tag can't be installed (network/auth), since a scaffold without the engine dep is not the story.
- An AC can't be met without changing the Architecture (name the section).
- You'd need credentials, cloud resources, or money.

## Final report shape

Per-AC ✅/❌ with file:line, test counts (before 0 / after N), the `ruff check` result, the verification
curl output, the commits (both remotes), and what STORY-005 (upload + preflight) should know: the exact
`create_job` signature, the Store and Settings construction pattern tests use, and how the app gets its
settings. Cite the tests as the contract, not hand-written JSON.
