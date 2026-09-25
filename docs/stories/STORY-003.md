# STORY-003: Engine: Book.cut_all with progress + v0.4.0

> **Status:** Done (2026-09-25)
> **Size:** S
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#Engine additions (monograph-splitter 0.4.0)`
> **Repo:** `~/Documents/Repos/monograph-splitter` (engine) — branch `story/STORY-003` off a PUSHED main

---

## Summary

As the web worker, I need one call that cuts every section with a progress callback, so that job progress is reportable and the CLI and web share one loop.

---

## Context

`cli.main` currently owns the loop over `Book.cut()`. Moving it into the session keeps the CLI a thin wrapper (the review server's principle).

---

## Depends On

- STORY-001 (entries/profile inputs)
- STORY-002 (so the release tag contains detect)

---

## Acceptance Criteria

- [ ] AC-1: `Book.cut_all(progress=None, verify=True, preview=False, only=None)` returns a summary `{written, flags, leaks, missing}` and calls `progress(done, total, name)` after each entry
- [ ] AC-2: `cli.main` uses `cut_all`; `monograph-splitter-diff` between a pre-change and post-change CLI run on the synthetic book = 0 and on Maciocia Foundations (real book, Inkwell tools dir) = 0
- [ ] AC-3: A web-mode end-to-end test: `profile_from_dict({...})` + `Book.open(entries=candidates-as-rows)` + `cut_all()` on the synthetic 2-column book writes 3 PDFs, the mid-column chapter's first page has the previous chapter redacted, verify reports 0 leaks
- [ ] AC-4: Version 0.4.0 in pyproject + ENGINE_VERSION; README documents web mode (`profile_from_dict`, `detect`, `cut_all`); tag `v0.4.0` pushed to Gitea

---

## Files Affected

| File | Change Type | Notes |
|------|-------------|-------|
| `src/monograph_splitter/session.py` | Modify | cut_all |
| `src/monograph_splitter/cli.py` | Modify | use cut_all |
| `src/monograph_splitter/__init__.py` | Modify | version |
| `pyproject.toml` | Modify | 0.4.0 |
| `README.md` | Modify | web mode section |
| `tests/test_web_mode.py` | Create | e2e |

---

## Implementation Notes

- ENGINE_VERSION is part of the index cache key: bumping it re-indexes Inkwell's books on next run (≈3–5 min each) — expected, mention in the commit.
- Real-book diff gate: follow [[monograph-splitter-uv-cache]] — `touch pyproject.toml` before running Inkwell adapters.

---

## Out of Scope

- Anything in the web repo.

---

## Verification Steps

```bash
cd ~/Documents/Repos/monograph-splitter && uv run --group dev pytest
# diff gate on the real book (from Inkwell's tools dir, per the adapter's README)
monograph-splitter-diff out/manifest.prev.json out/manifest.json
```

---

## E2E Test Plan

| Test | Type | What It Verifies |
|------|------|------------------|
| `tests/test_web_mode.py` | New | AC-3 |
| `tests/ (all)` | Existing | AC-2 synthetic |

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
feat: STORY-003 - Book.cut_all with progress; CLI on it; web mode documented (0.4.0)
```

---

## Status

**Done** — 2026-09-25 (engine `c8935d2` on `feature/web-mode`, tag `v0.4.0` on `origin` + `gitea`; findings `docs/findings/STORY-003-findings.md`)

- [x] AC-1
- [x] AC-2
- [x] AC-3
- [x] AC-4
