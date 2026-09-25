# STORY-001: Engine: settings-dict profiles and in-memory entries

> **Status:** Done (2026-09-25, engine 742bfbe on feature/web-mode)
> **Size:** S
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#Engine additions (monograph-splitter 0.4.0)`
> **Repo:** `~/Documents/Repos/monograph-splitter` (engine) — branch `story/STORY-001` off a PUSHED main

---

## Summary

As the web worker, I need to build a `Profile` from a JSON settings dict and open a `Book` from an in-memory section list, so that no TOML or JSON file is written per job.

---

## Context

The web service drives the engine with user-chosen settings (ADR-002/003). Today `Book.open` needs a profile path and an entries file.

---

## Depends On

- *(none)* — but first: the engine `main` has 1 unpushed commit (v0.3.1, 8eaa115) and an uncommitted README change from another session. Ask Shuma to push/commit those (or confirm) before branching; never `git add -A`.

---

## Acceptance Criteria

- [x] AC-1: `profile_from_dict(d)` returns a Profile built on `WEB_BASE` (headings mode, `sheet_offset=0` (spec said 1 — corrected, see findings), empty `script_regex`, no break patterns, `max_span=200`) overriding only `WEB_KEYS`; any other key raises `ProfileError` naming it
- [x] AC-2: `single_column: true` yields a profile where every line is in the left column and every cut is full-width (asserted on the synthetic book)
- [x] AC-3: Two dicts with the same values (any key order) produce the same `sha256`; different values produce different ones
- [x] AC-4: `entries_from_rows(rows)` is the validation `load_entries_json` now delegates to (same skipped/headings semantics); `load_entries_json` behaviour unchanged
- [x] AC-5: `Book.open(entries=[...])` accepts a list of rows or an `EntryList` as well as a Path; `profile=` accepts a Profile instance (already true — keep covered)
- [x] AC-6: Existing 51 tests pass; `monograph-splitter-diff` on a before/after manifest of the synthetic book = 0

---

## Files Affected

| File | Change Type | Notes |
|------|-------------|-------|
| `src/monograph_splitter/profile.py` | Modify | WEB_BASE, WEB_KEYS, profile_from_dict |
| `src/monograph_splitter/entries.py` | Modify | entries_from_rows |
| `src/monograph_splitter/session.py` | Modify | Book.open entries param |
| `tests/test_profile_dict.py` | Create |  |
| `tests/test_entries_rows.py` | Create |  |

---

## Implementation Notes

- Validate types with the existing `_check_type` (map WEB_KEYS → (table,key) via SCHEMA).
- `single_column` is not a Profile field: translate it to `column_split=0.999, full_width_ratio=0.0` (check `is_left`/`same_column` semantics before trusting those numbers).
- Hash = sha256 of `json.dumps(normalized, sort_keys=True)`.

---

## Out of Scope

- Detection (STORY-002), cut_all (STORY-003), tagging a release.

---

## Verification Steps

```bash
cd ~/Documents/Repos/monograph-splitter && uv run --group dev pytest
```

---

## E2E Test Plan

| Test | Type | What It Verifies |
|------|------|------------------|
| `tests/test_profile_dict.py` | New | AC-1..3 |
| `tests/test_entries_rows.py` | New | AC-4, AC-5 |
| `tests/ (all)` | Existing | AC-6 |

---

## Code Review Checklist

- [ ] No regressions or unintended side effects
- [ ] No security issues (injection, credential leaks, unsafe input)
- [ ] No changes outside this story's scope
- [ ] No dead code, debug artifacts, or leftover TODOs
- [ ] Style consistent with surrounding code

---

## Commit Message

```
feat: STORY-001 - profiles from a settings dict and in-memory entry lists (web mode)
```

---

## Status

**Done** — 2026-09-25 (see `docs/findings/STORY-001-findings.md`; WEB_BASE uses engine `sheet_offset=0` = page 1 is the first sheet)

- [x] AC-1
- [x] AC-2
- [x] AC-3
- [x] AC-4
- [x] AC-5
- [x] AC-6
