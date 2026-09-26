# Review — STORY-011
**Date:** 2026-09-25
**Reviewed:** pdf-splitter 68769d3 (feature/mvp)

## Round 1 — FAILED (3 confirmed)
Coverage highlights: live Maciocia split 15 s, 23 rows, 54 MB ZIP via plain links; poll resume without leaks; edit-after-cut → review with old links valid; Delete now removes the dir, gone screen on reload/second tab; tz display correct; XSS-safe names; no id in title/referrer.

1. [correctness] Expiry.svelte:36-46 (+ JobPage.svelte:73) — the CLIENT clock alone declares the job gone when expires_at passes; a visitor clock ahead of server UTC shows a live job as deleted (+25 h → deleted screen while API serves 200; +2 h → countdown 2 h short, deleted 1.5 h early). Spec flaw (kickoff gotcha 5). CONFIRMED.
2. [correctness] Review.svelte:145-147 — after a re-cut, a non-gone manifest failure (502/network) silently keeps the previous cut's rows under "Your files" (stale=false); links then download the wrong sections / cancel. No error, no retry. CONFIRMED.
3. [correctness] Download.svelte:47-56 — Split re-enabled between POST /cut returning and the restarted poll → double-click posts /cut twice → 409 busy message that outlives the cut. CONFIRMED.

## Round 2 (re-review of 9eeb9a2) — FAILED (2 confirmed: incomplete F1 + F3 regression) → auto-fix per standing policy
Verified: client clock ±25 h never decides; count-out → one poll → 410 → deleted; frozen tab OK; stale rows never shown; one POST per click across latencies; Maciocia loop.

1. [correctness, incomplete F1] Expiry.svelte:32,47,51 + JobPage.svelte:46 — countdown and count-out timer run only on performance.now(), which stops during OS suspend on Chromium/Linux; no wake/visibility re-check → after a laptop sleep the page overstates time left by the sleep length (24.5 h sleep past TTL: API 410, page "23 h" + links, 0 polls). CONFIRMED live.
2. [correctness, F3 regression] Download.svelte:52 (via :44) — `requestedOn` released only by a cut status whose state ≠ review; cut done + edit saved (done→review) before the first post-202 poll → first status is review/cut → Split disabled until reload; `cuts` never bumped so the list keeps old names. CONFIRMED (api.log corroboration).

## Round 3 (re-review of d1b4ce5, standing-policy auto-fix) — PASSED
Auto-review trail: r1 3 confirmed → fixed 9eeb9a2; r2 2 confirmed (suspend drift, stranded Split) → auto-fixed d1b4ce5; r3 CLEAN. Live: event-less suspend → 1 poll → server figure or deleted; bfcache/online/visibility re-checks; no storms (11 min idle = 2 polls; NTP step = 1 re-check; ±25 h client clock = 0 extra polls); single 1.5 s loop during a running cut; one POST per click; stranded-Split scenarios recover with the new manifest.
