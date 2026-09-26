# KICKOFF — STORY-001: Engine: settings-dict profiles and in-memory entries

## What you're walking into

Two repos, one product ("pdf-splitter": a public site where people drop a big PDF and get one
PDF per chapter/section back).

- **Engine** — `~/Documents/Repos/monograph-splitter` (GitHub `BigSpoon33/pdf-splitter-engine`,
  Python package `monograph_splitter`, v0.3.1). **You write code here**, on branch
  `feature/web-mode` (already checked out; main = 6fd22fc). Tests: `uv run --group dev pytest`
  → baseline **51 passed**.
- **Web/planning** — `~/Documents/Repos/pdf-splitter` on branch `feature/mvp`. Holds the PRD,
  Architecture, stories, and where you write findings + the next kickoff. Read
  `docs/Architecture.md` § "Engine additions (monograph-splitter 0.4.0)" and ADR-002/003 first,
  then `docs/stories/STORY-001.md` (the ACs are authoritative).

Engine layout (≈2.5k lines): `profile.py` (frozen `Profile` dataclass + TOML `SCHEMA` +
`_check_type` + `load_profile`), `entries.py` (`Entry`, `EntryList`, `load_entries_json`),
`session.py` (`Book.open(...)` — currently requires `entries=<Path>` or `from_vault=`),
`index.py`, `cuts.py`, `classify.py`, `render.py`, `verify.py`, `cli.py`,
`review/server.py`. Tests in `tests/` build synthetic two-column books (`tests/fixtures.py`);
no test reads a real book — keep it that way.

## What this story establishes

The web service will drive the engine with user-chosen settings and an in-memory section list —
no TOML/JSON files per job. Web mode = **headings mode** (`anchor_source="headings"`) with
`sheet_offset=1` (sheet numbers, not printed pages).

## Recommended ordering

1. `entries.entries_from_rows(rows: list) -> EntryList` — move the body of
   `load_entries_json` into it; `load_entries_json` becomes read-JSON + delegate. Behaviour
   identical (skipped reasons, `stop` rows, headings list, known_pages).
2. `profile.py`: `WEB_BASE = Profile(name="web", description=..., anchor_source="headings",
   sheet_offset=1, script_regex="", break_patterns=(), max_span=200, ...)`; `WEB_KEYS` = the
   whitelist from the Architecture (`column_split, header_band, footer_band, redact_top,
   heading_min_size, heading_match, heading_wrap_gap, max_span, single_column`);
   `profile_from_dict(d, base=WEB_BASE)`. Map each key to its `(table, key)` via `SCHEMA` so
   `_check_type` gives the same error text as TOML; unknown key → `ProfileError` naming it.
   Keep the same post-validation as `load_profile` (column_split range, etc.).
3. `single_column`: not a Profile field. Translate to values that make every line "left" and
   every cut full-width. **Read `classify.is_left`, `same_column`, `header_geometry` and
   `cuts.cut_rects` before choosing numbers** — prove it with a test on a synthetic 2-column
   page (every rect spans the full width). If `column_split` must stay < 1.0 per validation,
   pick a value that works and document why.
4. `sha256` = sha256 of `json.dumps(normalized_values, sort_keys=True)` so `index_book`'s
   cache key changes with settings.
5. `session.Book.open(entries=...)`: accept `Path | list[dict] | EntryList`.
6. Tests: `tests/test_profile_dict.py`, `tests/test_entries_rows.py`; full suite green.

## Conventions

- Python 3.11+, match the surrounding style (module docstrings, `from __future__ import
  annotations`, terse WHY comments, no WHAT comments).
- Commit on `feature/web-mode` in the engine repo:
  `feat: STORY-001 - profiles from a settings dict and in-memory entry lists (web mode)`
  ending with the line `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Stage explicit paths only — never `git add -A` / `git add .`.
- Do NOT bump the version or tag (STORY-003 does). Do NOT push the engine branch to main.
- Push the engine branch: `git push -u origin feature/web-mode` (GitHub). Gitea mirror
  (`gitea` remote) too: `git push gitea feature/web-mode`.
- Findings + next kickoff are committed in `~/Documents/Repos/pdf-splitter` on `feature/mvp`
  as `docs: STORY-001 - findings + KICKOFF-STORY-002`, pushed to `origin` and `gitea`.

## Authority

- Free: anything inside the engine repo's `src/` and `tests/`, README additions describing the
  new API, the pdf-splitter repo's `docs/findings/` and `docs/KICKOFF-*`.
- Do not touch: `~/Documents/AI/Inkwell` (another session works there), the engine's bundled
  profiles' values, the CLI's behaviour.

## Stopping conditions (BLOCKED protocol)

- The ACs can't be met without changing cut/classify behaviour for existing profiles.
- An existing test fails and the cause isn't your change.

## Final report shape

Per-AC ✅/❌ with file:line, test counts (before 51 / after N), commits (both repos), anything
STORY-002 (detect.py: outline + heading candidates) should know.
