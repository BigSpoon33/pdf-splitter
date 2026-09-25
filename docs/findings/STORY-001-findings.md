# Findings — STORY-001
**Date:** 2026-09-25
**Status:** done (one deliberate deviation in AC-1: `sheet_offset`, see Bugs Found)

Engine commit: `742bfbe` on `feature/web-mode` (`BigSpoon33/pdf-splitter-engine`, pushed to `origin` + `gitea`).

## AC Verification
- [x] AC-1: `profile_from_dict(d, base=WEB_BASE)` overrides only `WEB_KEYS`; any other key is a `ProfileError` naming it (`settings: unknown key(s) 'foo' (known: …)`). `WEB_BASE` = headings mode, empty `script_regex`, `break_patterns=()`, `max_span=200`, and page = **1-based sheet number**. That page mapping is engine `sheet_offset=0`, not 1 (see Bugs Found). Type errors reuse `_check_type` through a field→(table, key) map, so the text is identical to a TOML profile's (`[limits].max_span: expected int, got float (1.5)`). Post-validation is shared with `load_profile` (`_validated`). Out-of-range checks were added: bands/sizes ≥ 0, `heading_match` in (0, 1], `max_span` ≥ 1. — `src/monograph_splitter/profile.py:201` (`_validated`), `:223` (`WEB_BASE`), `:233` (`WEB_KEYS`), `:242` (`profile_from_dict`); tests `tests/test_profile_dict.py:20,28,45,51,70`
- [x] AC-2: `single_column: true` → `column_split=0.999`, `full_width_ratio=0.0` (`profile.py:238` `SINGLE_COLUMN`). `is_left` is `x0 < split·W`, so every line is left. `same_column` / `heading_anchor` call any line wider than 0·W full-width, so every heading anchor is `col="full"`, and `cut_rects` emits only `(0, y0, W, y1)` rects. 0.999 stays inside the `(0, 1)` validation. On the synthetic two-column `heading_book`, every line is left (and more than 50 of them sit in the right column), every anchor is full, and every rect spans x = 0…W. The control test shows the same book without `single_column` has left and right column cuts. — `tests/test_profile_dict.py:100,124`
- [x] AC-3: `sha256` = sha256 of `json.dumps(<every effective Profile field except path/sha256>, sort_keys=True)`. Key order doesn't change it, and neither does spelling out a default instead of leaving it out. Any changed value, including `single_column`, changes it. `index_book`'s cache key picks it up through `prof.sha256`. — `profile.py:273-276`; `tests/test_profile_dict.py:75`
- [x] AC-4: `entries_from_rows(rows, where="entries")` holds the old validation body, and `load_entries_json` = read JSON + delegate (the same error text, prefixed with the path). Skipped reasons, `stop` rows, `headings` and `known_pages` are unchanged. The row list and the JSON file give equal `EntryList`s. — `src/monograph_splitter/entries.py:49,53`; `tests/test_entries_rows.py:21,33`
- [x] AC-5: `Book.open(entries=…)` accepts `Path | str | list[dict] | EntryList`. An empty list is an empty book, not a missing argument, and no entries file is written. `profile=` accepts a `Profile` instance (covered with a `profile_from_dict` profile). — `src/monograph_splitter/session.py:63-74`; `tests/test_entries_rows.py:56,72,85`
- [x] AC-6: 51 existing tests pass (72 total). Regression gate: I generated manifests for both synthetic books (labels `scenario_book` + `profile-test.toml`, and headings `heading_book` + `profile-headings.toml`) through the CLI with `--verify`, once before and once after the change. `monograph-splitter-diff` gave `0 of 7 entries changed` (rc 0) and `0 of 6 entries changed` (rc 0).

## Test Results
**Command:** `cd ~/Documents/Repos/monograph-splitter && uv run --group dev pytest -q`
**Result:** pass
```
72 passed, 2 warnings in 2.66s        (before: 51 passed)
```
(The 2 warnings are pre-existing starlette/httpx deprecations.)

## Bugs Found
- **Spec bug: `sheet_offset = 1` is off by one.** PRD A-5, Architecture § Engine additions and ADR (line ~246), and AC-1 all say web mode uses "sheet numbers (PDF page 1 = first page, `sheet_offset = 1`)". The engine maps page N to sheet **index** `N + sheet_offset − 1` (`cuts.plan`, `Book.sheet_of`; `tests/profile-test.toml` uses 0 for "printed page N is sheet N−1"). So `sheet_offset=1` would put page 1 on the *second* sheet. I implemented the intent: `WEB_BASE.sheet_offset = 0`, and a test pins "page 1 → sheet index 0" (`tests/test_entries_rows.py:72`, `tests/test_profile_dict.py:20`). **The docs (PRD A-5, Architecture, ADR) should be corrected to "`sheet_offset = 0` (page N = 1-based sheet N)"**. I did not edit them (outside my authority).
- **Would-be bug avoided:** `cut_rects` removes a full-width strip from `redact_top` to `subheader_bottom` (default 72) above any column start (the "previous entry's running name"). A web book has no running sub-header, so with the default that strip (y 45–72) would be redacted on every column-start page. `WEB_BASE.subheader_bottom = redact_top`, and a user `redact_top` carries it along, which makes that rect zero-height. `profile-headings.toml` does the same thing (45/45).

## Handoff Context for Next Session
Web "page" = 1-based sheet = engine `sheet_offset 0`: `detect.*` must emit 1-based sheet numbers, and `profile_from_dict` profiles then plan them correctly. `WEB_BASE` still carries two defaults a generic book may trip over. `title_min_y=30` means `locate_heading` ignores lines whose top is above y=30. `chapter_only` (`^\s*chapter\s*\d+…`, ≥14 pt, ≤28 chars) still marks chapter-break sheets, which end the previous entry. Neither is a web key yet.

## Out-of-Scope Items
- Correcting `sheet_offset = 1` in PRD A-5 / Architecture / ADR (docs owner).
- Whether `WEB_BASE` should disable `chapter_only` (e.g. `(?!)`) and lower `title_min_y` for arbitrary PDFs. Decide once STORY-002/003 run on real outlines.
- `long_span` stays at 10, so any web section longer than 10 sheets is flagged `long-span`. The UI story may want it raised or ignored.
- No version bump or tag (STORY-003).
