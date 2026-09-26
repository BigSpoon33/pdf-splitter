# Review — STORY-015
**Date:** 2026-09-26
**Reviewed:** pdf-splitter 722b231 (feature/mvp)

## Round 1 — FAILED (1 confirmed)
Coverage highlights: PRD AC-14 passes end to end; parser handles dashes/whitespace/per-token errors; validation (endPage required/forbidden/bounds, overrides refused) sound; ranges cut runs in the same sandbox; chapter flow on Maciocia identical (23 files, 53,874,969 B ZIP).

1. [correctness] Review.svelte:113 (+ editor.svelte.ts:160-164) — ANY job opened with `?mode=ranges` (incl. an edited, already-cut chapter job) is converted to an empty `ranges` plan and saved, no confirmation, no undo; `source` then keeps it in range mode forever — chapter list and manual cuts unrecoverable. CONFIRMED.
Scheduled elsewhere (not a 015 defect): unbounded output volume — 2,000 whole-book spans write ~0.9 GB (Maciocia) or ~19 GB (image-heavy PDF) per 10-min cut; chapter mode has the same class → STORY-012 output cap.

## Round 2 (re-review of 469dbee) — PASSED
Auto-review: 1 confirmed → fixed 469dbee (mode set at upload via form field + mode.json; URL never writes a plan), re-review CLEAN. 20+ link variants, two tabs, pushState, requeue → 0 writes; PRD AC-14 end to end; Maciocia chapter flow identical within the engine's byte noise.
