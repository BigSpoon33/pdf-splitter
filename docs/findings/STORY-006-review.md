# Review — STORY-006
**Date:** 2026-09-25
**Reviewed:** pdf-splitter 12b4e9b (feature/mvp)

## Round 1 — FAILED (2 confirmed)
Coverage highlights: /proc limits on the live task = CPU 310 / FSIZE 1 GiB / AS 2 GiB; WORKERS cap 2 of 3 live; real MuPDF CPU loop killed at 5.0 s, no zombies; deleted-mid-run stays deleted; SIGTERM drains; ids never in logs; preflight rlimits don't regress STORY-005.

1. [ac-gap] worker/task.py:90-100 (`guarded`) — a MuPDF allocation failure under RLIMIT_AS surfaces as `RuntimeError("code=2: calloc (...) failed")`, not MemoryError → `internal` instead of AC-4's `resources`. Repro: scratchpad/rv006/xbomb6.pdf (4,852 B, page-2 nested XObjects; passes preflight) → VmPeak = AS cap → failed/internal. CONFIRMED.
2. [correctness] worker/task.py:63 — `write_text(json.dumps(..., ensure_ascii=False))` is strict UTF-8; an outline title PyMuPDF returns with lone surrogates (`/Title <EFBBBF4368617074FF6572>` or `<FEFF00430068D800>`) raises UnicodeEncodeError → whole analysis failed/internal, empty `analysis.json.tmp` left behind. CONFIRMED.
