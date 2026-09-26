# STORY-016: Engine: per-sheet geometry for cut rectangles (mixed page sizes) + v0.4.2

> **Status:** Done (2026-09-26)
> **Size:** S
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#engine-additions-monograph-splitter-040`
> **Repo:** `~/Documents/Repos/monograph-splitter` (engine) — branch `feature/web-mode`, then pin in pdf-splitter

---

## Summary

As a visitor with a book whose pages differ in size, I need every cut computed in that sheet's own width/height, so that the downloaded PDFs are cut exactly where the preview shows.

## Context

STORY-010's round-2 review: `Book.rects` / `write_excerpt` (and `cut_rects` callers) use the section's FIRST sheet W/H for every sheet; on a section spanning two page sizes the last sheet's redaction rectangles (gutter x, band edges, full-width spans) are wrong in the output. The SPA preview now draws per-sheet geometry, so preview and output disagree on such books.

## Depends On

- STORY-003 (engine v0.4.1)

## Acceptance Criteria

- [x] AC-1: every redaction rectangle for sheet s uses sheet s's own `page.rect` (W, H) — gutter at `column_split × W_s`, bands/limits in that sheet's H — in `cut_rects`/`Book.rects`/`write_excerpt` and anything else computing geometry per sheet.
- [x] AC-2: synthetic test: a two-size book (522.72×789.6 then 700×600) with a section spanning both; the written excerpt's last page keeps exactly the right-column/left-column content the plan says (assert on text positions), and `verify` reports 0 leaks.
- [x] AC-3: regression gate: `monograph-splitter-diff` 0 changed on the synthetic books AND on Maciocia / Chen & Chen (uniform-size books must be byte-identical in decisions); `ENGINE_VERSION` bumped only if the index changes (it shouldn't).
- [x] AC-4: release `v0.4.2` (tag on both remotes; never move v0.4.1); pdf-splitter pins `@v0.4.2`, `uv lock`, full web suite green; the SPA's per-sheet hatch now matches output on mixed.pdf (manual check, screenshot).

## Files Affected

| File | Change Type | Notes |
|------|-------------|-------|
| engine `src/monograph_splitter/cuts.py`, `session.py`, `render.py` | Modify | per-sheet W/H |
| engine `tests/test_mixed_sizes.py`, `tests/fixtures.py` | Create/Modify | two-size book |
| pdf-splitter `pyproject.toml`, `uv.lock`, `docs/Architecture.md` | Modify | pin v0.4.2 |

## Out of Scope

- Rotated pages beyond what `page.rect` already normalises.

## Verification Steps

```bash
cd ~/Documents/Repos/monograph-splitter && uv run --group dev pytest -q
cd ~/Documents/Repos/pdf-splitter && uv run pytest -q && (cd web && bun run test)
```

## E2E Test Plan

| Test | Type | What It Verifies |
|------|------|------------------|
| engine `tests/test_mixed_sizes.py` | New | AC-1, AC-2 |
| diff gate on real books | Existing | AC-3 |

## Code Review Checklist

- [ ] No regressions or unintended side effects
- [ ] No security issues (injection, credential leaks, unsafe input)
- [ ] No changes outside this story's scope
- [ ] No dead code, debug artifacts, or leftover TODOs
- [ ] Style consistent with surrounding code

## Commit Message

```
fix: STORY-016 - cut rectangles use each sheet's own size (0.4.2)
```

## Status

**Done** — 2026-09-26 (engine `116a4bb` = `v0.4.2`; pin `c410caa`; findings `docs/findings/STORY-016-findings.md`)

- [x] AC-1
- [x] AC-2
- [x] AC-3
- [x] AC-4
