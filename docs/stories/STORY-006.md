# STORY-006: Web: worker runner, sandbox, analyze task

> **Status:** Done (2026-09-25)
> **Size:** M
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#worker`
> **Repo:** `~/Documents/Repos/pdf-splitter`

---

## Summary

As a visitor, I need my upload analyzed in the background with visible progress, so that I get candidate sections without the server hanging.

---

## Context

Wires the engine (STORY-001..003) into the service safely (ADR-005).

---

## Depends On

- STORY-003 (engine 0.4.0)
- STORY-005

---

## Acceptance Criteria

- [ ] AC-1: `pdf-splitter worker` loops: claim a queued job, run `python -m pdf_splitter.task analyze <id>` with rlimits (AS 2 GB, CPU timeout+10, FSIZE 1 GB) and wall timeout `ANALYZE_TIMEOUT`, at most `WORKERS` concurrent
- [ ] AC-2: Analyze task: index with the default web profile (cache in `work/`), `outline_levels` + `outline_entries` for each level present (≤3), `heading_candidates`, page labels, page sizes → writes `analysis.json` (Architecture shape) incl. `suggested` (outline level 1 if it has ≥2 items, else the heading level whose count is closest to 5–60) → state `review`
- [ ] AC-3: Progress: the task reports `progress/total` (pages indexed) to the job row at most every 0.5 s
- [ ] AC-4: A task exceeding its timeout or memory is killed → `failed` with `error_code` `timeout`|`resources`; an exception → `failed/internal`; the loop keeps running (test with an injected slow/crashing task)
- [ ] AC-5: On worker start, jobs `running` longer than their timeout are re-queued once, then failed
- [ ] AC-6: Also writes the default `plan.json` from `suggested`

---

## Files Affected

| File | Change Type | Notes |
|------|-------------|-------|
| `src/pdf_splitter/worker/{runner,task,analyze}.py` | Create |  |
| `tests/test_worker.py` | Create |  |
| `tests/fixtures/` | Create | synthetic books (import builder from engine tests or port) |

---

## Implementation Notes

- Engine's `index_book(log=...)` has no per-page progress hook; either add an optional `progress` param in the engine (then it belongs in STORY-003 — check first) or index via `index_page` per page in the task. Prefer the engine hook.
- `resource.setrlimit` in `preexec_fn`.

---

## Out of Scope

- Cut task (STORY-007), network isolation (compose, STORY-013).

---

## Verification Steps

```bash
uv run pytest tests/test_worker.py
```

---

## E2E Test Plan

| Test | Type | What It Verifies |
|------|------|------------------|
| `tests/test_worker.py` | New | AC-1..6 |

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
feat: STORY-006 - sandboxed worker runner and analyze task (outline + heading candidates)
```

---

## Status

**Done** (2026-09-25)

- [x] AC-1
- [x] AC-2
- [x] AC-3
- [x] AC-4
- [x] AC-5
- [x] AC-6
