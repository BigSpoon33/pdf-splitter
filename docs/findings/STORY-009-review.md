# Review — STORY-009
**Date:** 2026-09-25
**Reviewed:** pdf-splitter 784b31d (feature/mvp)

## Round 1 — FAILED (6 confirmed, 3 refuted)
Coverage highlights: manifest route sound (capability, 409/410, atomic zip, no ids); heading_wrap_gap validation/omission/cache sound; PlanEditor one-in-flight + version guard sound; paste parser sound; AC-1/4/5 sound; no {@html}.

Confirmed:
2. plan.ts:115-121 / :142-150 — removeSection/insertSection keep the neighbour's END override although that section's end moved (engine applies endCut on its new last sheet → text cut out of every file; differs from mergeWithNext). Engine-verified on headed_book.
4. Review.svelte:69-70 + JobPage.svelte:15 — manifest fetched only in `done`; after any saved edit (→ review) + reload, last-cut badges and the "cut again" note vanish though GET /manifest returns them.
5. SourcePicker.svelte:63-64 — paste box seeded only when empty → later switch to "Paste a list" silently replaces the list with stale text (bad lines dropped, no "Use this list" gate).
6. editor.svelte.ts:75,86 — Undo restores the whole plan snapshot, reverting+saving settings changed after the switch.
7. editor.svelte.ts:204-207 — destroy() drops a pending debounced save (also no beforeunload/pagehide flush).
8. SourcePicker.svelte:33-35,90 — Undo doesn't resync the picker's level/threshold controls.
Refuted: 1 (version guard works; but trailing-space loss mid-typing is real UX → orchestrator addition), 3 (index+name badge matching is the documented conservative design; merge case → small orchestrator addition), 9 (story never asked; PRD bands/max-length heading filters unscheduled → orchestrator addition).

## Round 2 (re-review of 9e29032) — FAILED (1 confirmed: fix-regression + incomplete F7) → auto-fix per standing policy
Verified: all 6 findings + 3 additions work (3000-plan randomized override property test clean; live browser checks); every new test fails on 784b31d.

1. [correctness, regression] editor.svelte.ts:257-263 (+ :100-102, :267; api.ts:242-249) — `sendBeforeUnload` always uses fetch keepalive; Chromium refuses keepalive bodies > ~64 KiB and `unsent` is already cleared, so a refused send is never retried; `onVisibility` routes an ordinary tab switch through it. Maciocia "Any level" = 1654 sections (142 KB): tab switch → "Could not reach the server", later close → rename lost (784b31d saved it). CONFIRMED (Chromium cutoff between 65,536 and 70,000 B).
