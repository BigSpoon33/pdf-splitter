# STORY-002: Engine: outline and big-heading candidate detection

> **Status:** Done (2026-09-25)
> **Size:** M
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#Engine additions (monograph-splitter 0.4.0)`
> **Repo:** `~/Documents/Repos/monograph-splitter` (engine) — branch `story/STORY-002` off a PUSHED main

---

## Summary

As a user with an arbitrary PDF, I need the engine to propose sections from the PDF outline or from large headings, so that I don't have to type an entry list.

---

## Context

This is the new capability that makes a drag-and-drop UX possible (PRD scope 1+2). It is the riskiest piece (A-1).

---

## Depends On

- *(none)* — parallel to STORY-001 (same engine branch base rules)

---

## Acceptance Criteria

- [ ] AC-1: `detect.outline_entries(doc, level)` returns `[{name, page, heading, level, y?}]` for every outline item at exactly `level` (1-based sheets); `y` present when the destination carries a point
- [ ] AC-2: `detect.outline_levels(doc)` returns counts per level (for the UI's level picker); a PDF without outline → `[]`
- [ ] AC-3: `detect.heading_candidates(doc, min_ratio, max_len, header_band, footer_band, wrap_gap)` computes body size as the char-weighted modal glyph size and returns candidates ≥ `body × min_ratio`, outside the bands, ≤ `max_len` chars, wrapped lines merged
- [ ] AC-4: Candidates carry `level` from size clustering (largest = 1, sizes within 0.5 pt share a level) and `col` (left/right/full via `column_split`)
- [ ] AC-5: On a synthetic 2-column book with 3 chapters (1 starting mid-right-column) and body 9.5 pt / chapter 16 pt / section 12 pt: level-1 candidates = the 3 chapters with correct pages and cols; raising `min_ratio` above 16/9.5 returns none
- [ ] AC-6: Running headers (same text on ≥ 30% of pages in the header band) and page-number-only lines are never candidates
- [ ] AC-7: Pure functions: no files written; ≤ one `get_text('dict')` per page (reuse `index.page_lines`)

---

## Files Affected

| File | Change Type | Notes |
|------|-------------|-------|
| `src/monograph_splitter/detect.py` | Create |  |
| `tests/fixtures.py` | Modify | builder for a generic headed book with outline |
| `tests/test_detect.py` | Create |  |

---

## Implementation Notes

- `doc.get_toc(simple=False)` → `[lvl, title, page, dest]`; `dest['to']` is a Point in PDF coords (y from bottom in some versions — convert and test).
- Headings mode's `locate_heading` fuzzy-matches `heading` on the sheet; outline titles often differ in case/numbering ("1 Introduction" vs "INTRODUCTION"). Normalise in `outline_entries` only by stripping leading numbering; do NOT change `locate_heading`.
- If outline `y` exists, STORY-003/007 can pass it through; the engine plumbing for a direct y anchor is a follow-up unless trivial (`heading_anchor` needs a located line — find the line nearest y).

---

## Out of Scope

- Labels-mode detection; OCR; any UI.

---

## Verification Steps

```bash
cd ~/Documents/Repos/monograph-splitter && uv run --group dev pytest tests/test_detect.py
```

---

## E2E Test Plan

| Test | Type | What It Verifies |
|------|------|------------------|
| `tests/test_detect.py` | New | AC-1..7 |

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
feat: STORY-002 - detect: outline entries and big-heading candidates for arbitrary books
```

---

## Status

**Done** — 2026-09-25 (engine `ef828ab` + gate r1 fix `4d8d375` + gate r2 fix `defb734` on `feature/web-mode`; findings `docs/findings/STORY-002-findings.md`)

- [x] AC-1
- [x] AC-2
- [x] AC-3
- [x] AC-4
- [x] AC-5
- [x] AC-6
- [x] AC-7
