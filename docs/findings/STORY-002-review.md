# Review — STORY-002
**Date:** 2026-09-25
**Status:** round 1 FAILED (3 confirmed findings) → retry
**Reviewed:** engine 742bfbe..ef828ab (feature/web-mode)

Round 1: story-reviewer verdict FINDINGS; 3 findings, all 3 CONFIRMED by independent finding-skeptics (repros in the orchestrator scratchpad: probe3.py, real5.py, probe4.py, probe5.py).

## Confirmed findings (verbatim)

1. [correctness] src/monograph_splitter/detect.py:189-192 — the wrap merge only joins lines whose `_col` class is identical, so a wrapped heading whose lines straddle the full-width threshold (one line > full_width_ratio·W = "full", the other narrower = "left"/"right") is never merged, even with the same x0; `locate_heading` (index.py:229) groups by `is_left(x0)` only and would join them. AC-3 "wrapped lines merged" unmet.
   - Synthetic: 612×792, default geometry, wrap_gap=16, 13 pt heading at x0=72, baselines 15.6 pt apart: "Differential Diagnosis of the Principal Patterns of Disharmony in" (0.70·W) / "Clinical Practice" (0.21·W) → two level-2 candidates (col full / col left) instead of one.
   - Real: Maciocia, wrap_gap=30: ch. 30 p480 "Identification of Patterns" (0.47·W, left) + "according to the Eight Principles" (0.61·W, full) → Book plans a one-sheet sliver (480–480) + a misnamed chapter (480–497). Same for ch. 31 (p498).
   - No test covers a wrap straddling full/left or full/right.
2. [correctness] src/monograph_splitter/detect.py:25 (applied :186) — `_PAGE_NUMBER`'s `[ivxlcdm]+` alternative (re.I) matches any word spelled only with those letters, so real headings are discarded as page numbers. Glossary with one 20 pt letter per page: C D I L M V X missing (B absorbs C, D); 20 pt headings "Dill", "Mild", "Civil", "Mix", "Mid", "Mill", "Vivid", "Ill" never candidates. AC-3 violated; AC-6 only excludes page-number-ONLY lines.
3. [ac-gap] src/monograph_splitter/detect.py:46-60 — `_dest_top` handles only "array"/"string"/"name" kinds from `xref_get_key`; an indirect destination (`/D n 0 R` or `/Dest n 0 R`, kind "xref") falls through and no y is emitted. `/A <</S/GoTo/D 16 0 R>>` with `16 0 obj [p 0 R /XYZ 0 589.6 null]` → no `y`, while get_toc reports to=Point(0, 200) and the direct array gives y=200.0. AC-1 unmet for this input.

## Round 2 — 2026-09-25 — FAILED (2 confirmed) → loop HALTED
**Reviewed:** engine 742bfbe..4d8d375. r1's 3 fixes verified working (straddle merge, roman-word headings mid-page, indirect dests). Two new confirmed findings share ONE root cause: `_at_edge` (detect.py:157-160) floors the page-edge strip at `EDGE_SHARE·H` (0.12·H ≈ 95 pt on Letter) regardless of the caller's `header_band`/`footer_band`, and page-opening headings sit inside that strip (top ≈ 60–70 pt).

1. [correctness] detect.py:175 (+ :157-160, :33, :220) — a well-formed roman-numeral line in that strip is dropped as a folio: page-opening glossary letters C D I L M V X (20 pt, baseline 90, top 68.6) are never candidates, under any band/min_ratio setting. The r1 glossary test passes only because its letters sit at top 98.6. Repro: scratchpad/probe_glossary.py, probe_glossary2.py.
2. [correctness] detect.py:153-154 (+ :163-171, :220) — `_running_key` masks digits, so distinct page-opening headings "Lesson 1..10" (≥30% of pages, inside the 0.12·H strip) collapse into one "running header" and are removed everywhere → `candidates=[]` for a 30-page workbook of 3-page lessons; no setting recovers them. `test_a_header_repeated_on_30_percent_of_pages_is_running_and_below_that_is_not` bakes this in ("Rare Header 1/2/3"). Repro: scratchpad/probe_numbered.py, probe_numbered2.py.

Per AutoLoop policy (2 failed gates) the loop is HALTED pending Shuma's decision.

## Round 3 — 2026-09-25 — 1 confirmed regression (within the approved attempt-3 scope)
**Reviewed:** engine 742bfbe..defb734. All 5 prior findings verified fixed; Maciocia candidates identical r1→r2; AC-1..7 hold under the Attempt-3 rules.

1. [correctness, regression from 4d8d375] detect.py:54-55 (`_dest_value`; reached from `_dest_top` :69-70, "A/D" tried before "Dest") — an out-of-range `N 0 R` goes straight to `doc.xref_object(N)` → `RuntimeError: bad xref`, uncaught in `outline_entries` (:118-133), so the WHOLE level's rows are lost (ef828ab fell through to the valid `/Dest`). Repro: scratchpad/r3/repro_dangling_A.py (also `/D 0 0 R`). CONFIRMED by skeptic (ef828ab vs defb734 side by side).

Follow-ups (non-blocking, logged for a detection-tuning story):
- detect.py:231 — a heading wrapped over 4+ lines disappears once wrap_gap joins it (MAX_WRAP_LINES `continue`); keeping its first 3 lines would still locate (ratio 0.877).
- default wrap_gap 16 (= heading_wrap_gap) < normal leading of ≥14 pt headings → wrapped big titles split by default; a size-scaled web default feeding both belongs with STORY-009 settings.
- per-line max span size: one inline big glyph (∑ at 14 pt in 9.5 pt text) promotes a body line to a candidate.

## Round 3b — 2026-09-25 — PASSED
**Reviewed:** engine defb734..8c12b3f. Auto-review trail: r1 3 confirmed → fixed (4d8d375); r2 2 confirmed → loop halted → Shuma approved attempt 3 → fixed (defb734); r3 1 confirmed regression → fixed (8c12b3f); r3b re-review CLEAN. Regression sweep: 79 real outlined PDFs (20,040 rows) identical defb734 vs 8c12b3f, 0 errors.
