# STORY-004: Web: repo scaffold, settings, SQLite store, health

> **Status:** Pending
> **Size:** S
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#store`
> **Repo:** `~/Documents/Repos/monograph-splitter-web`

---

## Summary

As the developer, I need a runnable FastAPI skeleton with config, the jobs store and tests, so that every later story has a home.

---

## Context

The first code in the new repo.

---

## Depends On

- *(none)* (engine dep pinned to the v0.3.1 tag until STORY-003 lands; bump then)

---

## Acceptance Criteria

- [ ] AC-1: `uv run mss-web api` serves `GET /api/health` → `{ok, queue, disk_free_gb, engine_version}`
- [ ] AC-2: Settings via pydantic-settings, env prefix `MSS_`: `JOBS_DIR`, `MAX_BYTES` (200 MB), `MAX_PAGES` (2000), `TTL_HOURS` (24), `WORKERS` (2), `RATE_PER_HOUR` (6), `ANALYZE_TIMEOUT` (300), `CUT_TIMEOUT` (600), `PUBLIC_URL`
- [ ] AC-3: `store.py`: schema per Architecture, WAL mode, `create_job`, `get_job`, `claim_next(kind)` (atomic, `BEGIN IMMEDIATE`), `update_progress`, `set_state`, `expired()`; two concurrent claimers never claim the same job (test with threads)
- [ ] AC-4: Job ids are 16 random bytes url-safe base64; logs use `sha256(id)[:8]`
- [ ] AC-5: `uv run pytest` green; ruff clean; README with dev commands

---

## Files Affected

| File | Change Type | Notes |
|------|-------------|-------|
| `pyproject.toml` | Create | package mss_web, deps, scripts |
| `src/mss_web/{__init__,config,store,app}.py` | Create |  |
| `tests/test_store.py` | Create |  |
| `README.md` | Create |  |

---

## Implementation Notes

- Package name `mss_web`. Python 3.12. Engine dep: `monograph-splitter @ git+https://git.gumshu.duckdns.org/shuma/monograph-splitter@v0.4.0` (NOTE: the deployed VM cannot reach gumshu — Gitea is LAN-only. Vendor via a wheel build in CI or mirror the engine to a public remote before STORY-014; record the choice in Architecture).

---

## Out of Scope

- Upload, worker, frontend.

---

## Verification Steps

```bash
uv run pytest && uv run ruff check && (uv run mss-web api & sleep 2; curl -s localhost:8000/api/health)
```

---

## E2E Test Plan

| Test | Type | What It Verifies |
|------|------|------------------|
| `tests/test_store.py` | New | AC-3, AC-4 |
| `tests/test_health.py` | New | AC-1 |

---

## Code Review Checklist

- [ ] No regressions or unintended side effects
- [ ] No security issues (injection, credential leaks, unsafe input)
- [ ] No changes outside this story's scope
- [ ] No dead code, debug artifacts, or leftover TODOs
- [ ] Style consistent with surrounding code

---

## Commit Message

```
feat: STORY-004 - scaffold: FastAPI app, settings, SQLite job store, health
```

---

## Status

**Pending**

- [ ] AC-1
- [ ] AC-2
- [ ] AC-3
- [ ] AC-4
- [ ] AC-5
