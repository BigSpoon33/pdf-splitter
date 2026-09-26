# Review — STORY-016
**Date:** 2026-09-26
**Reviewed:** engine 8e52cc3..116a4bb (v0.4.2); web c410caa, a9932a9

## Round 1 — FAILED (1 confirmed test gap; code correct)
Coverage highlights: new cut_rects vs an oracle of v0.4.1 per sheet — 891,000 combos, 0 mismatches; 126 real mixed-size plans (odd first sheet, 3–4 sizes, same-sheet start+end) clean; diff gate re-run: synthetic 0 changed, Maciocia 0/211 (211 PDFs byte-identical), Chen & Chen 0/231 (230 byte-identical; the one mixed-span section differs by 23 B, same text); tags intact on both remotes; web pin/lock verified.

1. [test-gap] engine tests/test_mixed_sizes.py:102-109 — the review-PNG test checks only pixel size (set by the page itself); reverting render.py:43/:48 to sheet-0 geometry keeps 156/156 green while the CLI `--preview` PNG hatches the wrong region on the last sheet. Mutation-proven by the reviewer. (render_review is engine-CLI only; the web never calls it.)

## Round 2 (orchestrator check of engine 4f1644a, test-only) — PASSED
4f1644a touches only tests/test_mixed_sizes.py (no src); v0.4.2 still → 116a4bb, v0.4.1 → 8e52cc3; test_mixed_sizes 6/6 green; mutation-proven (render.py :43 and :48 reverted → fails).
