# Review — STORY-010
**Date:** 2026-09-25
**Reviewed:** pdf-splitter e0a10cd (feature/mvp)

## Round 1 — FAILED (5 confirmed)
Coverage highlights: overlay geometry matches engine on Maciocia two-column sheets; drags send 0 requests mid-gesture, 1 POST + 1 PUT on release; clamping, pointer capture, touch work; keyboard nudges + aria sliders; rapid switching ends on the right section; object URLs revoked; 410 → gone, no retry loop; no {@html}.

1. [correctness] PagePreview.svelte:67-72 — `fail()` marks the editor `gone` for ANY `isGone` error, incl. the section-plan route's 404 not_found "no section with that number" (routes/preview.py:116), which the preview provokes by asking for a LOCAL index before the save lands (insert above/at the selection shifts `selected`). → editor.gone, all later edits silently never saved, beforeunload silenced. CONFIRMED.
2. [correctness] PagePreview.svelte:94-95 — dedup key built from the LOCAL list while POST /sections/{i}/plan plans the SAVED list; delete/merge above the selection → preview shows the wrong section and never refreshes when the save lands; a drag there saves an override for the wrong span. CONFIRMED.
3. [correctness] PagePreview.svelte:366 (+184-189, 375, 380) — every sheet uses the FIRST sheet's size for aspect/viewBox/pointer mapping; mixed-size books → stretched sheet, end cut drawn/dragged ~90 pt off. CONFIRMED.
4. [correctness] PagePreview.svelte:223 — `preventDefault()` on pointerdown stops the grip taking focus; after a drag, arrow keys go to the previously focused control (the section radio group → switches section). CONFIRMED.
5. [correctness] PagePreview.svelte:141/159/351 — `sheetError` never cleared on selection change → stale alert over a healthy preview. CONFIRMED.

## Round 2 (re-review of 7a28b6e) — PASSED
Auto-review: 5 confirmed → fixed 7a28b6e, re-review CLEAN. Validation parity with PUT /plan (29 lists); 410 beats 422; nothing persisted; drag accuracy ≤0.1 pt on mixed sizes; focus/keys; errors per selection.
Engine observation (scheduled as STORY-016): `Book.rects`/`write_excerpt` compute every cut rectangle in the FIRST sheet's W/H — mixed-size sections get wrong cuts in the OUTPUT PDFs.
Perf notes (follow-ups): 2,000-section preview ≈18–20 s (at the 20 s timeout); a landed rename triggers one extra preview (7–9 s on 1,654 sections) — key/send the list without names.
