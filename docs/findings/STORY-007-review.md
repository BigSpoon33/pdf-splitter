# Review — STORY-007
**Date:** 2026-09-25
**Reviewed:** pdf-splitter 6fcf848 (feature/mvp)

## Round 1 — FAILED (1 confirmed)
Coverage highlights: Plan validation airtight (NaN/Inf/1e999, page 0/pages+1, 200k sections in 0.17 s, surrogates, override bounds); no path traversal (ints only); zip-slip-safe names (`../../etc/passwd`, RLO, `/`); Content-Disposition injection-proof; state machine (double cut, PUT/cut while running, DELETE mid-cut) holds; preview sandboxed (AS 2 GB/CPU 30/20 s); section-plan timings 0.4–4.1 s on Maciocia; request ids + redacted tracebacks; 0 raw ids in logs.

1. [correctness] preview.py:36 (+ routes/preview.py:72-76, routes/download.py:76-77) — an in-flight sheet render recreates `<jobs>/<id>/png/<dpi>/` after DELETE removed the job dir (`mkdir(parents=True)`), writes the page PNG, and get_sheet serves it (200) without re-checking the row; the file then survives until TTL (janitor only sweeps expired rows / row-less dirs). Violates AC-5 + PRD AC-10. CONFIRMED live. Related: the section-plan preview after DELETE fails with 500 `preview_failed` instead of 410.

## Round 2 (re-review of 3058d91) — FAILED (1 confirmed, incomplete fix of r1-1) → auto-fix per standing policy
Verified live on Maciocia: 162 deletes across sheet renders, bursts, cached and re-indexing section plans → only 200/410, 0 × 500, 0 surviving dirs, 0 raw ids; `settled`'s rmtree only fires on terminal `deleted` (never a live/expired job); 4 new tests fail on 6fcf848.

1. [ac-gap] routes/preview.py:101-104 — `get_sheet` calls `settled` BEFORE reading the rendered PNG, so a DELETE between them → `_cached` None → 500 `preview_failed` instead of 410 (spec item 2). Deterministic via a settled-then-delete hook; live 3/3 with a widened window; nothing recreated. CONFIRMED.
