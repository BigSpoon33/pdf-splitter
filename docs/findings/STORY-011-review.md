# Review — STORY-011
**Date:** 2026-09-25
**Reviewed:** pdf-splitter 68769d3 (feature/mvp)

## Round 1 — FAILED (3 confirmed)
Coverage highlights: live Maciocia split 15 s, 23 rows, 54 MB ZIP via plain links; poll resume without leaks; edit-after-cut → review with old links valid; Delete now removes the dir, gone screen on reload/second tab; tz display correct; XSS-safe names; no id in title/referrer.

1. [correctness] Expiry.svelte:36-46 (+ JobPage.svelte:73) — the CLIENT clock alone declares the job gone when expires_at passes; a visitor clock ahead of server UTC shows a live job as deleted (+25 h → deleted screen while API serves 200; +2 h → countdown 2 h short, deleted 1.5 h early). Spec flaw (kickoff gotcha 5). CONFIRMED.
2. [correctness] Review.svelte:145-147 — after a re-cut, a non-gone manifest failure (502/network) silently keeps the previous cut's rows under "Your files" (stale=false); links then download the wrong sections / cancel. No error, no retry. CONFIRMED.
3. [correctness] Download.svelte:47-56 — Split re-enabled between POST /cut returning and the restarted poll → double-click posts /cut twice → 409 busy message that outlives the cut. CONFIRMED.
