# STORY-009: SPA: source picker, section list, layout panel

> **Status:** Pending
> **Size:** M
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#web (SPA)`
> **Repo:** `~/Documents/Repos/pdf-splitter`

---

## Summary

As a visitor, I need to choose how sections are found and edit the list and layout, so that the split matches my book.

---

## Context

The core of 'tweak the settings' (PRD scope).

---

## Depends On

- STORY-007, STORY-008

---

## Acceptance Criteria

> **Orchestrator decision (2026-09-25, from STORY-008 findings):** flags need a JSON source, so this story also adds ONE small backend route: `GET /api/jobs/{id}/manifest` → the last cut's manifest rows (by plan section index; 409 `not_ready` before any cut; 410 for deleted/expired; ids never logged). Architecture § API Interface lists it. Pin it with a pytest test; the SPA reads it for the badges.

- [ ] AC-1: SourcePicker: Outline (level select with counts; disabled with an explanation when the PDF has none) / Headings (threshold slider in ×body size, level select, live count) / Paste list (`Name, page` per line; parse errors shown per line)
- [ ] AC-2: Switching source replaces the section list (with an undo toast); the choice + list persist via `PUT /plan` (debounced 600 ms)
- [ ] AC-3: SectionList: rename, delete, add (name + page), change page, merge-with-next; flags from the last cut shown as badges; page shown as sheet number + printed label when available
- [ ] AC-4: LayoutPanel: one/two columns toggle, gutter % (number input; drag lands in STORY-010), header/footer band inputs, heading size threshold
- [ ] AC-5: 422 field errors from the API render next to the offending control
- [ ] AC-6: Component tests: paste-list parser (good/bad lines), merge-with-next, source switch + undo

---

## Files Affected

| File | Change Type | Notes |
|------|-------------|-------|
| `web/src/components/{SourcePicker,SectionList,LayoutPanel}.svelte` | Create |  |
| `web/src/lib/plan.ts` | Create | plan store + parser |

---

## Implementation Notes

- Heading candidates are computed once in analysis at a low threshold; filtering by threshold/level is client-side (instant).

---

## Out of Scope

- Page previews (STORY-010).

---

## Verification Steps

```bash
cd web && bun run check && bun run test
```

---

## E2E Test Plan

| Test | Type | What It Verifies |
|------|------|------------------|
| `web/src/lib/plan.test.ts` | New | AC-1, AC-3, AC-6 |

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
feat: STORY-009 - source picker, editable section list and layout panel
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
