# Review — STORY-006
**Date:** 2026-09-25
**Reviewed:** pdf-splitter 12b4e9b (feature/mvp)

## Round 1 — FAILED (2 confirmed)
Coverage highlights: /proc limits on the live task = CPU 310 / FSIZE 1 GiB / AS 2 GiB; WORKERS cap 2 of 3 live; real MuPDF CPU loop killed at 5.0 s, no zombies; deleted-mid-run stays deleted; SIGTERM drains; ids never in logs; preflight rlimits don't regress STORY-005.

1. [ac-gap] worker/task.py:90-100 (`guarded`) — a MuPDF allocation failure under RLIMIT_AS surfaces as `RuntimeError("code=2: calloc (...) failed")`, not MemoryError → `internal` instead of AC-4's `resources`. Repro: scratchpad/rv006/xbomb6.pdf (4,852 B, page-2 nested XObjects; passes preflight) → VmPeak = AS cap → failed/internal. CONFIRMED.
2. [correctness] worker/task.py:63 — `write_text(json.dumps(..., ensure_ascii=False))` is strict UTF-8; an outline title PyMuPDF returns with lone surrogates (`/Title <EFBBBF4368617074FF6572>` or `<FEFF00430068D800>`) raises UnicodeEncodeError → whole analysis failed/internal, empty `analysis.json.tmp` left behind. CONFIRMED.

## Round 2 (re-review of e15e7f0) — FAILED (2 confirmed) → loop HALTED (not a fix-regression; policy says ask)
Verified: reviewer's bomb + variants → resources; 9 invalid-Unicode titles → review with strict-UTF-8 files; valid text (emoji, CJK Ext-B, combining, ZWJ, RTL, PUA) unchanged; no genuine internal error misclassified.

1. [ac-gap, incomplete fix of r1-1] worker/task.py:110-114 — the allocator branch only inspects `RuntimeError`; PyMuPDF's raw bindings raise `pymupdf.mupdf.FzErrorSystem` (Exception subclass) for the same allocator failure (load_page, xref_*, resolve_names, page labels) → still `internal`. Repro: rv006r2/link_uri_40k.pdf (309 KB, 40k links sharing a 64 KB URI) → `FzErrorSystem: code=2: malloc (65537 bytes) failed` → failed/internal. Also `page_labels()`' guard doesn't catch FzError*. CONFIRMED.
2. [test-gap] tests/test_worker.py:317-319 — the `.tmp`-cleanup test is vacuous (fails in json.dumps before the .tmp exists); removing the cleanup keeps all 81 worker tests green while a real mid-write EFBIG leaves a 4 KB .tmp. CONFIRMED (production code is correct).

## Round 3 (re-review of 5f15a36, Shuma-approved attempt 3) — PASSED
Auto-review trail: r1 2 confirmed → fixed e15e7f0; r2 2 confirmed (incomplete fix + vacuous test) → halted → Shuma approved attempt 3 + widened policy → fixed 5f15a36; r3 CLEAN. Real-sandbox evidence: load_page bomb (link URIs), resolve_names bomb (new, 40k named dests) → resources; parent → internal; page-label allocator bomb → review with blank labels (by spec: labels never fail a job); non-alloc errors stay internal; .tmp tests mutation-proven (4 mutants).
