# Review — STORY-003
**Date:** 2026-09-25
**Reviewed:** engine 8c12b3f..c8935d2 (tag v0.4.0)

## Round 1 — FAILED (1 confirmed)
Coverage highlights: CLI byte-identical across 10 configs; diff gate 0/211 Maciocia, 0/231 CC-F, 0/305 CC-H (all manifest fields + index pages identical); guard touches no Inkwell names (~1,650 checked); Inkwell's imported symbols intact.

1. [correctness] review/server.py:266-269 — the new `try/except ValueError` wraps `cfg_of(book_id).book` (lazy `Book.open`) too, so any ValueError from opening the book (JSONDecodeError from a malformed overrides.json/manifest.json/entries JSON, ProfileError, "give entries=…") becomes 404 "no such excerpt" instead of surfacing (8c12b3f: 500). Repro: scratchpad/gate/srv/probe.py vs probe_pre.py. CONFIRMED by skeptic.
