# STORY-008: SPA: scaffold, drop zone, job status

> **Status:** Pending
> **Size:** S
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#web (SPA)`
> **Repo:** `~/Documents/Repos/pdf-splitter`

---

## Summary

As a visitor, I need to drop a PDF on the page and watch it upload and analyze, so that I know it's working.

---

## Context

First visible product.

---

## Depends On

- STORY-005 (upload API); STORY-006 for real status (mock until then)

---

## Acceptance Criteria

- [ ] AC-1: `web/` is a Vite + Svelte + TS app; `bun install && bun run dev` proxies `/api` to :8000; `bun run build` emits static files; `bun run check` (svelte-check) clean
- [ ] AC-2: DropZone accepts drag-drop and click-to-pick, prechecks `.pdf`/type and `MAX_BYTES` client-side, shows upload % (XHR progress)
- [ ] AC-3: After upload the URL becomes `/j/<id>`; JobStatus polls every 1.5 s while queued/running and shows state, queue position, and `progress/total`; reloading `/j/<id>` resumes
- [ ] AC-4: API error codes map to human messages (a single `errors.ts` table covering every code in Architecture)
- [ ] AC-5: Component tests (vitest + @testing-library/svelte) for DropZone precheck and the error table

---

## Files Affected

| File | Change Type | Notes |
|------|-------------|-------|
| `web/` | Create | Vite+Svelte+TS |
| `web/src/lib/api.ts` | Create | typed client |
| `web/src/lib/errors.ts` | Create |  |
| `web/src/components/{DropZone,JobStatus}.svelte` | Create |  |

---

## Implementation Notes

- Bun only (never npm). Keep deps minimal: svelte, vite, typescript, svelte-check, vitest, @testing-library/svelte.
- Styling: plain CSS with tokens on :root + dark mode; mobile-friendly drop zone.

---

## Out of Scope

- Review UI (STORY-009+).

---

## Verification Steps

```bash
cd web && bun run check && bun run test && bun run build
```

---

## E2E Test Plan

| Test | Type | What It Verifies |
|------|------|------------------|
| `web/src/**/*.test.ts` | New | AC-2, AC-4 |

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
feat: STORY-008 - SPA scaffold with drop zone and live job status
```

---

## Status

**Pending**

- [ ] AC-1
- [ ] AC-2
- [ ] AC-3
- [ ] AC-4
- [ ] AC-5
