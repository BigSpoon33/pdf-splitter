# Findings — STORY-002
**Date:** 2026-09-25
**Status:** done

Engine commit: `ef828ab` on `feature/web-mode` (`BigSpoon33/pdf-splitter-engine`), pushed to `origin` + `gitea`.

## AC Verification
- [x] AC-1: `detect.outline_entries(doc, level)` returns `[{name, page, heading, level, y?}]` in outline order, for items at exactly `level` only. `page` is PyMuPDF's 1-based page, which is the web "page" (1-based sheet, engine `sheet_offset=0`). Items with page -1 (broken or external links) or an empty title are dropped. `name` is the whitespace-collapsed title and `heading` is the same title with its leading number stripped (`1`, `1.2.3`, `2.`, `IV.`, `iv)`; words such as "A …" or "I Ching" are kept). The **raw** destination decides `y`, because PyMuPDF reports `/Fit` and `/XYZ null null` as `(0, 0)` and gives a named destination's point unconverted (y up). `y` is present for `/XYZ` with a numeric top, `/FitH`, `/FitBH`, `/FitR` and named destinations that resolve to a non-zero point. It is converted to page coordinates (y down) with `page.transformation_matrix`. — `src/monograph_splitter/detect.py:39` (`_dest_top`), `:95` (`outline_entries`); tests `tests/test_detect.py:37,51,70`
- [x] AC-2: `detect.outline_levels(doc)` returns `[{level, count}]` sorted by level and counts the same items `outline_entries` would return. A PDF with no outline returns `[]`. — `detect.py:89`; `tests/test_detect.py:29`
- [x] AC-3: `detect.heading_candidates(doc, *, min_ratio=1.3, max_len=90, header_band, footer_band, wrap_gap, column_split, full_width_ratio)`. The last five default to `Profile`'s defaults (50 / 32 / 16 / 0.487 / 0.55). Body size is the char-weighted mode: characters vote in 0.5 pt bins, then the result is the char-weighted mean inside the winning bin. A candidate has size ≥ `body × min_ratio` and top in `[header_band, H − footer_band)`. Wrapped lines are merged when they share a column, their tops are ≤ `wrap_gap` apart and their sizes are within 0.5 pt. A merge takes at most 3 lines, because `locate_heading` joins at most 3. `max_len` applies to the joined text. — `detect.py:116` (`body_size`), `:161`; tests `tests/test_detect.py:78,91,108`
- [x] AC-4: `level` comes from clustering the distinct sizes in descending order: a new level starts when a size is more than 0.5 pt below the current level's largest size. `levels` = `[{size (the level's largest), count}]`. `col` is `full` if the merged block is wider than `full_width_ratio·W`, otherwise `left`/`right` by `x0 < column_split·W`, which is the rule `heading_anchor` uses. Candidates come out in page order and, within a page, in `finalize_anchors`' reading order. — `detect.py:147,153`, level loop at the end of `heading_candidates`; tests `tests/test_detect.py:114,141,157`
- [x] AC-5: `tests/fixtures.py:195` `headed_book`: a 6-sheet two-column book with body 9.5 pt, chapters 16 pt bold (left at the top of p1, full-width at the top of p3, **mid-right-column on p4**), 12 pt sections (one wrapped over two lines in the right column), a 14 pt running header on every page and a 12.5 pt page number in the footer. The level-1 candidates are exactly the 3 chapters with pages 1/3/4 and cols left/full/right. The level-2 candidates are the 4 sections. `min_ratio = 16/9.5 + 0.01` returns no candidates and no levels. — `tests/test_detect.py:127`
- [x] AC-6: A running header or footer is text (digits → `#`) at a given size (0.5 pt bin) that sits in the page-edge strip on ≥ 30% of pages, with a minimum of 2 pages. The strip is `max(header_band, 0.12·H)` at the top and `max(footer_band, 0.12·H)` at the bottom, so a band that is set too thin still catches it. Page-number-only lines (`^\W*(page\s*)?(\d+|roman)\W*$`) are excluded anywhere on the page. The tests use the 14 pt header and 12.5 pt numbers with `header_band=0`/`footer_band=0`, show the 20% vs 30% boundary, and cover `12`, `- 12 -`, `xiv`, `Page 3`, `PAGE 214` and a dash-wrapped number. — `detect.py:130,134`; tests `tests/test_detect.py:167,185,192`
- [x] AC-7: Nothing is written and the document stays clean (`not doc.is_dirty`, the cwd stays empty, the PDF's bytes and mtime are unchanged). `heading_candidates` calls `page.get_text` exactly once per page, through `index.page_lines`. The outline functions never call it. — `tests/test_detect.py:204`
- Round-trip (the kickoff's contract): level-1 candidates go into `Book.open(entries=rows, profile=profile_from_dict({}))` with 0 `heading-not-found` and sheet0 = 0/2/3. Every candidate, the level-1 outline rows and the level-2 outline rows also plan with no `heading-not-found`, under `heading_min_size: 11.5` because the sections are 12 pt. — `tests/test_detect.py:233,243`
- Orchestrator addendum: the `profile.py` WEB_BASE comment no longer says the docs write `sheet_offset = 1`.

## Test Results
**Command:** `cd ~/Documents/Repos/monograph-splitter && uv run --group dev pytest -q`
**Result:** pass
```
102 passed, 2 warnings in 3.62s        (before: 72 passed; +30 in tests/test_detect.py)
```
The 2 warnings are pre-existing starlette/httpx deprecations. `cuts`, `index`, `classify` and `locate_heading` were not touched, so the synthetic manifests cannot change.

## Real-book sanity check (Maciocia *Foundations*, 1319 sheets, read-only, nothing written)
- `outline_levels`: L1 23 · L2 339 · L3 449 · L4 810 · L5 2250 · L6 1193 · L7 553 · L8 39 · L9 11 (5667 items). Every L1/L2 row has `y` (the book uses `/FitH`), e.g. `{'name': 'Front cover', 'page': 1, 'heading': 'Front cover', 'level': 1, 'y': 0.3}`, `{'name': 'Introduction', 'page': 31, …, 'level': 2, 'y': 50.3}`, `{'name': 'End Notes', 'page': 31, …, 'y': 396.3}`, and at L3 `{'name': 'Nature of Diagnosis by Interrogation', 'page': 366, …, 'y': 144.3}`. L1 is front matter plus parts. The chapters are L2.
- `heading_candidates` (defaults) takes **13.4 s**, finds body 9.5 pt and **1654 candidates in 15 levels**: 120 / 70 / 58 / 50 / 43.3 / 35 / 29 / 24 (89 = chapter titles) / 21.7 / 21 / 18 / 16 / 15.4 / 14 / 13 (1493 = section and pattern headings, where 12.5 and 13 cluster together). Levels 1–7 are cover and chapter-opener display type. **"Level 1" is not "chapters" on a real book.** The UI needs the `levels` list (size + count) to pick from, and should not default to level 1.
- MuPDF prints `syntax error: invalid key in dict` 9× on stderr inside `doc.get_toc()` for this file. The noise comes from the file and the output is still complete.

## Bugs Found
- None in the new code.
- **Pre-existing, matters for web mode (STORY-003):** `Book.excerpt_path(name)` = `out / f"{name}.pdf"` uses the **raw entry name as a filename**. A detected name containing `/` (Maciocia's candidates include `https://evolve.elsevier.com/Maciocia/foundations/`) writes into a subdirectory or fails, and `../` in a user-edited name escapes `out`. Duplicate names ("Notes", "Introduction" and "End Notes" repeat across Maciocia's L2) overwrite one another's PDF. `Book.entry(name)` and `overrides.json` are also keyed by name, so the second duplicate can't be addressed. Detection returns names as they are and does not dedupe. The web layer or `cut_all` must make names unique and filename-safe.

## Handoff Context for Next Session
Detection takes an open `fitz.Document`, but `Book.open` takes a **path** and opens its own. The web worker opens the PDF twice, once for detect and once for the Book. The contract is the tests: `test_level_1_candidates_open_a_book_and_every_heading_is_located` is the shape STORY-003's e2e extends with `cut_all()`. Candidate rows carry extra keys (`size`, `level`, `y`, `col`); `entries_from_rows` ignores them.

## Out-of-Scope Items
- Using the outline `y` / candidate `y` as a direct anchor (the story says it's a follow-up unless trivial). `locate_heading` still searches the whole sheet. On a page where the heading text repeats, `y` would disambiguate.
- Filename-safe / unique entry names (see Bugs Found): STORY-003 or the web job layer.
- A web-exposed `min_ratio` / level-default policy for real books (see the sanity check). This belongs to the UI story.
- `WEB_BASE.chapter_only` still applies. The synthetic book avoids "Chapter N" as a bare line. A real book whose chapter heading is exactly "Chapter 3" would mark that sheet as a chapter break. This was already noted in the STORY-001 findings.
