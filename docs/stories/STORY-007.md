# STORY-007: Web: plan, preview, cut and download API

> **Status:** Pending
> **Size:** M
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#api`
> **Repo:** `~/Documents/Repos/monograph-splitter-web`

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

- [ ] AC-1: `GET/PUT /api/jobs/{id}/plan`: PUT validates with Pydantic (sources enum, settings within ranges: column_split 0.2–0.8, bands 0–200 pt, heading_min_size 4–72; ≤ 2,000 sections; names deduped + ≤120 chars; pages within 1..pages) → 422 with field errors
- [ ] AC-2: `GET /sheets/{n}.png?dpi=` renders via a preview subprocess (20 s timeout), cached per (sheet, dpi, settings-hash); dpi outside {48,72,110} → 422
- [ ] AC-3: `POST /sections/{i}/plan` returns the engine's plan view incl. redaction `rects` for the section under the given settings/override, without persisting
- [ ] AC-4: `POST /cut` queues a cut; the cut task builds the profile with `profile_from_dict`, opens the Book with the plan's sections, applies overrides, runs `cut_all(verify=True)` with progress, writes `result.zip` (`NNN - <slug>.pdf` + manifest.json) → `done`
- [ ] AC-5: `GET /result.zip` and `/sections/{i}.pdf` stream as attachments; `DELETE` removes dir + marks row `deleted`; expired → 410
- [ ] AC-6: End-to-end API test on the synthetic book: upload → analyze → PUT plan (headings source) → cut → zip has 3 PDFs + manifest, 0 leaks

---

## Files Affected

| File | Change Type | Notes |
|------|-------------|-------|
| `src/mss_web/routes/{plan,preview,download}.py` | Create |  |
| `src/mss_web/worker/cut.py` | Create |  |
| `src/mss_web/models.py` | Create | Pydantic Plan etc. |
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

**Pending**

- [ ] AC-1
- [ ] AC-2
- [ ] AC-3
- [ ] AC-4
- [ ] AC-5
- [ ] AC-6
