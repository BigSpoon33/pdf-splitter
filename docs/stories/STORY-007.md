# STORY-007: Web: plan, preview, cut and download API

> **Status:** Done (2026-09-25)
> **Size:** M
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#api`
> **Repo:** `~/Documents/Repos/pdf-splitter`

---

## Summary

As a visitor, I need to save my section plan, preview cuts, run the cut and download the results, so that the UI has everything it needs.

---

## Context

Completes the backend contract in Architecture › API Interface.

---

## Depends On

- STORY-006

---

## Acceptance Criteria

- [x] AC-1: `GET/PUT /api/jobs/{id}/plan`: PUT validates with Pydantic (sources enum, settings within ranges: column_split 0.2–0.8, bands 0–200 pt, heading_min_size 4–72; ≤ 2,000 sections; names deduped + ≤120 chars; pages within 1..pages) → 422 with field errors
- [x] AC-2: `GET /sheets/{n}.png?dpi=` renders via a preview subprocess (20 s timeout), cached per (sheet, dpi, settings-hash); dpi outside {48,72,110} → 422
- [x] AC-3: `POST /sections/{i}/plan` returns the engine's plan view incl. redaction `rects` for the section under the given settings/override, without persisting
- [x] AC-4: `POST /cut` queues a cut; the cut task builds the profile with `profile_from_dict`, opens the Book with the plan's sections, applies overrides, runs `cut_all(verify=True)` with progress, writes `result.zip` (`NNN - <slug>.pdf` + manifest.json) → `done`
- [x] AC-5: `GET /result.zip` and `/sections/{i}.pdf` stream as attachments; `DELETE` removes dir + marks row `deleted`; expired → 410
- [x] AC-7: Unexpected errors (any route) return 500 JSON `{code:"internal", message, request_id}` with an `X-Request-ID` header; the same request id appears in the access-log line and the logged traceback (no raw job id anywhere — reuse the STORY-005 redaction). Every request gets an id (inbound `X-Request-ID` ignored unless it matches `[A-Za-z0-9-]{8,64}`). Test with a route forced to raise. (Scheduled by the orchestrator from STORY-005 review finding 2.)
- [x] AC-6: End-to-end API test on the synthetic book: upload → analyze → PUT plan (headings source) → cut → zip has 3 PDFs + manifest, 0 leaks

---

## Files Affected

| File | Change Type | Notes |
|------|-------------|-------|
| `src/pdf_splitter/routes/{plan,preview,download}.py` | Create |  |
| `src/pdf_splitter/worker/cut.py` | Create |  |
| `src/pdf_splitter/models.py` | Create | Pydantic Plan etc. |
| `tests/test_api_e2e.py` | Create |  |

---

## Implementation Notes

- Path params are ints validated against the job — never join user strings into paths.
- Preview pool: keep it simple first (subprocess per call); add the LRU of open Books only if a preview takes >1 s.

---

## Out of Scope

- Rate limits (STORY-012); UI.

---

## Verification Steps

```bash
uv run pytest
```

---

## E2E Test Plan

| Test | Type | What It Verifies |
|------|------|------------------|
| `tests/test_api_e2e.py` | New | AC-1..6 |

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
feat: STORY-007 - plan/preview/cut/download API and the cut task
```

---

## Status

**Done** — 2026-09-25, `6fcf848` on `feature/mvp` (326 tests, ruff clean; findings: `docs/findings/STORY-007-findings.md`)

- [x] AC-1
- [x] AC-2
- [x] AC-3
- [x] AC-4
- [x] AC-5
- [x] AC-6
- [x] AC-7
