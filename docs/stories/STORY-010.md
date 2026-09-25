# STORY-010: SPA: page preview with cut overlays and draggable cuts

> **Status:** Pending
> **Size:** M
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#web (SPA)`
> **Repo:** `~/Documents/Repos/monograph-splitter-web`

---

## Summary

As a visitor, I need to see exactly what each output PDF will contain and drag the cut lines, so that mid-page and two-column boundaries come out right.

---

## Context

The 'handles columns' promise made visible; ports the engine review editor's overlay idea.

---

## Depends On

- STORY-009

---

## Acceptance Criteria

- [ ] AC-1: Selecting a section shows its first and last sheet PNGs with an SVG overlay: gutter line, header/footer bands, hatched removed regions from the section plan's `rects`, and the start/end cut lines
- [ ] AC-2: Dragging a cut line updates `startCut`/`endCut` (and the column the drag started in sets `startCol`/`endCol`); release re-fetches the section plan and saves the override in the Plan
- [ ] AC-3: Dragging the gutter line sets `column_split` for the whole book; bands are draggable too
- [ ] AC-4: 'Reset' clears a section's override; sections with overrides show a badge
- [ ] AC-5: Works with keyboard: arrow keys nudge the focused cut line by 1 pt (Shift = 10 pt)
- [ ] AC-6: Coordinates: PNG pixel ↔ PDF pt conversion is unit-tested (dpi 72/110, non-zero CropBox origin)

---

## Files Affected

| File | Change Type | Notes |
|------|-------------|-------|
| `web/src/components/PagePreview.svelte` | Create |  |
| `web/src/lib/geometry.ts` | Create | px↔pt |

---

## Implementation Notes

- Prior art: `monograph-splitter/src/monograph_splitter/review/app.html` (hatching, ruler, band semantics). Port the math, not the code.

---

## Out of Scope

- Multi-page thumbnails strip (nice-to-have, later).

---

## Verification Steps

```bash
cd web && bun run check && bun run test
```

---

## E2E Test Plan

| Test | Type | What It Verifies |
|------|------|------------------|
| `web/src/lib/geometry.test.ts` | New | AC-6 |
| `manual in browser` | Manual | AC-1..5 (screenshot via Browser skill) |

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
feat: STORY-010 - page preview with hatched cuts and draggable cut, gutter and band lines
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
