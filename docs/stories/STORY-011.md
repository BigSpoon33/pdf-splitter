# STORY-011: SPA: cut, download, delete and expiry UX

> **Status:** Pending
> **Size:** S
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#web (SPA)`
> **Repo:** `~/Documents/Repos/pdf-splitter`

---

## Summary

As a visitor, I need to run the split, download the results and delete my files, so that I leave with my PDFs and nothing lingers.

---

## Context

Closes the user loop.

---

## Depends On

- STORY-010

---

## Acceptance Criteria

- [ ] AC-1: 'Split' button posts `/cut`, shows progress per section, then a results list (per-section download links + flags) and 'Download all (ZIP)'
- [ ] AC-2: Editing anything after a cut returns to review state; previous downloads stay until the next cut
- [ ] AC-3: 'Delete now' (confirm) calls DELETE and shows a deleted screen; the expiry time is always visible ('files deleted in 23 h')
- [ ] AC-4: Expired/410 job URL shows the deleted screen with a 'Split another PDF' link
- [ ] AC-5: Footer links to /privacy and /terms (static pages, content in STORY-014)

---

## Files Affected

| File | Change Type | Notes |
|------|-------------|-------|
| `web/src/components/{Download,Expired}.svelte` | Create |  |
| `web/src/routes or App.svelte` | Modify |  |

---

## Implementation Notes



---

## Out of Scope

- Terms/privacy text.

---

## Verification Steps

```bash
cd web && bun run check && bun run test && bun run build
```

---

## E2E Test Plan

| Test | Type | What It Verifies |
|------|------|------------------|
| `web/src/**/*.test.ts` | New | AC-2, AC-4 state transitions |

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
feat: STORY-011 - split, download, delete-now and expiry UX
```

---

## Status

**Pending**

- [ ] AC-1
- [ ] AC-2
- [ ] AC-3
- [ ] AC-4
- [ ] AC-5
