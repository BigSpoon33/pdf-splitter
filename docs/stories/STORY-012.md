# STORY-012: Limits: rate limiting, disk guard, janitor, queue position

> **Status:** Pending
> **Size:** S
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#janitor`
> **Repo:** `~/Documents/Repos/pdf-splitter`

---

## Summary

As the operator, I need uploads rate-limited, disk bounded and old files deleted, so that one visitor can't starve or fill the service.

---

## Context

PRD AC-10, AC-11.

---

## Depends On

- STORY-007

---

## Acceptance Criteria

- [ ] AC-1: Per-IP (salted-hash, daily-rotating salt) sliding-window limit `RATE_PER_HOUR` on `POST /api/jobs` → 429 `rate_limited` with `Retry-After`; client IP from `X-Forwarded-For` only when the peer is the trusted proxy
- [ ] AC-2: Uploads refused with 503 `disk_full` when free space on JOBS_DIR < `MIN_FREE_GB` (2)
- [ ] AC-3: Janitor (in the worker process, every 5 min): deletes expired job dirs + marks rows `deleted`, deletes orphan dirs, prunes `rate` rows and deleted rows older than 7 days
- [ ] AC-4: `GET /api/jobs/{id}` includes `queue_position` for queued jobs
- [ ] AC-5: Tests with a frozen clock: TTL expiry, orphan removal, rate window, disk guard (monkeypatched `shutil.disk_usage`)

---

## Files Affected

| File | Change Type | Notes |
|------|-------------|-------|
| `src/pdf_splitter/{ratelimit,janitor}.py` | Create |  |
| `src/pdf_splitter/worker/runner.py` | Modify | janitor loop |
| `tests/test_limits.py` | Create |  |

---

## Implementation Notes

- Confirm Q-3 caps with Shuma before merging (defaults stand otherwise).

---

## Out of Scope

- Captcha / proof-of-work (only if abuse appears).

---

## Verification Steps

```bash
uv run pytest tests/test_limits.py
```

---

## E2E Test Plan

| Test | Type | What It Verifies |
|------|------|------------------|
| `tests/test_limits.py` | New | AC-1..5 |

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
feat: STORY-012 - rate limit, disk guard, 24 h janitor and queue position
```

---

## Status

**Pending**

- [ ] AC-1
- [ ] AC-2
- [ ] AC-3
- [ ] AC-4
- [ ] AC-5

> **Orchestrator addendum (from STORY-007 review):** the janitor must also remove the directory of any row in state `deleted` (defence in depth against a late write recreating it), not only expired rows and row-less dirs. Test it.
