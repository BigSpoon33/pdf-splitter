# Review — STORY-001
**Date:** 2026-09-25
**Status:** passed
**Reviewed:** engine 6fd22fc..742bfbe (feature/web-mode)

Auto-review: clean (story-reviewer verdict CLEAN; no findings to skeptic-verify)

## Coverage (reviewer)
- AC-1..AC-6 verified with file:line evidence (profile.py:201-276, entries.py:49-78, session.py:65-74, tests/test_profile_dict.py, tests/test_entries_rows.py).
- Regression gate reproduced from git-archive exports: 0 changed on all 4 synthetic book/profile pairs, and on the REAL Maciocia Foundations PDF (1319 sheets, 211 entries, --verify) — rows byte-identical; ENGINE_VERSION + TOML hashes unchanged, so Inkwell's index caches stay valid.
- Accepted spec correction: web mode uses engine `sheet_offset=0` (page N → 0-based sheet index N+offset−1); PRD A-5, Architecture, ADR-003 and AC-1 corrected by the orchestrator.

## Carried forward
- Cosmetic: comment at profile.py:218-219 still says docs write "sheet_offset = 1" (fix in STORY-002).
- WEB_BASE defaults not named by the contract (chapter_only regex active, title_min_y 30, long_span 10) — decide in STORY-003 (web e2e will show whether they bite).
- NaN/inf floats pass profile_from_dict range checks — the API's Plan validation (STORY-007) must reject them.
