# STORY-015: Home page with two entry points + page-range mode

> **Status:** Done (2026-09-26)
> **Size:** M
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#adr-009-page-range-mode-is-a-plan-source-not-a-new-pipeline`
> **Repo:** `~/Documents/Repos/pdf-splitter`

---

## Summary

As a visitor, I need to choose between "Split by chapters" and a plain "Split by page ranges", so that the simple split everyone expects is one click away next to the smart one.

---

## Context

Shuma (2026-09-25): the site is a focused tool in the iLovePDF/Smallpdf style — upload, work, download, auto-delete — framed as "split your book into chapters", with a page-range entry point added now. PRD AC-14, ADR-009.

---

## Depends On

- STORY-011 (cut/download/delete UX this reuses)

---

## Acceptance Criteria

- [ ] AC-1: The home page shows two clearly labelled entry points — **Split by chapters** (the existing flow) and **Split by page ranges** — each with its own drop zone (or one drop zone with a mode toggle); the chosen mode survives the upload redirect and a reload (`/j/<id>?mode=ranges`, or a plan `source`).
- [ ] AC-2: Backend: `PlanSettings`/`Plan` accept `source: "ranges"` and optional per-section `endPage` (inclusive, ≥ page, ≤ pages); ranges may overlap or leave gaps; in `ranges` mode `endPage` is required and overrides are rejected (422). Existing sources are unchanged (`endPage` forbidden or ignored there — pick one, test it).
- [ ] AC-3: Backend: the cut task for a `ranges` plan copies each `[page, endPage]` span with PyMuPDF into its own PDF (no heading search, no redaction), writes `result.zip` + `manifest.json` in the same shape (flags empty), with the same atomic-zip/cleanup guarantees, sandbox and progress as the chapter cut. pytest: 30-page fixture, `1-10, 15-20, 5-7` → 3 PDFs of 10/6/3 pages with the right page text.
- [ ] AC-4: SPA range editor: a text field accepting `1-10, 11-25, 40` (single pages allowed), validated live with per-token errors (out of range, reversed, malformed), plus an "every N pages" helper that fills the ranges; names default to `Pages 1–10`; the resulting sections list reuses the existing list UI where sensible (rename/delete). No source picker, preview or layout panel in this mode.
- [ ] AC-5: Split → download works exactly as in chapter mode (STORY-011 UI); PRD AC-14's verification passes end-to-end in a headless browser.
- [ ] AC-6: Tests: parser (ranges, every-N, errors), plan validation (pytest), ranges cut (pytest, real PDFs), home page mode choice + reload (vitest). `bun run check` 0 warnings; Python suite + ruff green.

---

## Files Affected

| File | Change Type | Notes |
|------|-------------|-------|
| `src/pdf_splitter/models.py` | Modify | `ranges` source, `endPage` |
| `src/pdf_splitter/worker/cut.py` | Modify | ranges cut path |
| `web/src/App.svelte`, `web/src/components/Home*.svelte`, `web/src/components/RangeEditor.svelte` | Create/Modify | two entry points, range editor |
| `web/src/lib/ranges.ts` (+ tests) | Create | parser / every-N |
| `docs/Architecture.md`, `README.md` | Modify | if anything differs from ADR-009 |

---

## Implementation Notes

- Reuse `write_zip`/manifest helpers and the job/state machine; do not fork the upload or download paths.
- Engine names: same `NNN-<slug>` rule; display names editable.
- Keep the chapter flow's behaviour byte-identical (its tests must stay green).

---

## Out of Scope

- Merge/compress/convert or any other hub tool (v2 PRD).

---

## Verification Steps

```bash
uv run pytest -q && uv run ruff check
cd web && bun run check && bun run test && bun run build
```

---

## E2E Test Plan

| Test | Type | What It Verifies |
|------|------|------------------|
| `tests/test_ranges.py` | New | AC-2, AC-3 |
| `web/src/lib/ranges.test.ts`, home/editor component tests | New | AC-1, AC-4, AC-6 |
| headless browser run | Manual | AC-5 / PRD AC-14 |

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
feat: STORY-015 - home page with chapter and page-range entry points; page-range split mode
```

---

## Status

**Done** — 2026-09-26, `722b231` on feature/mvp + gate r1 fix `fix: STORY-015 - gate r1: the split mode is fixed at upload, a link can never convert a job` (`docs/findings/STORY-015-findings.md` § Gate r1 fix)

- [x] AC-1
- [x] AC-2
- [x] AC-3
- [x] AC-4
- [x] AC-5
- [x] AC-6
