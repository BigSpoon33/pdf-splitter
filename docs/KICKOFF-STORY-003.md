# KICKOFF — STORY-003: Engine: Book.cut_all with progress + v0.4.0

## What you're walking into

Two repos make up one product, "pdf-splitter": a public site where someone drops in a big PDF and gets back one
PDF per chapter or section.

- **Engine**: `~/Documents/Repos/monograph-splitter` (GitHub `BigSpoon33/pdf-splitter-engine`,
  Python package `monograph_splitter`, still v0.3.1, PyMuPDF 1.28.2). **You write code here**, on
  branch `feature/web-mode`. `main` = 6fd22fc. On the branch: `742bfbe` (STORY-001) → **`ef828ab`**
  (STORY-002, detect), both pushed to `origin` and `gitea`. The story file says "branch
  `story/STORY-003` off a PUSHED main", but the loop keeps one engine branch, `feature/web-mode`,
  for every engine story. Stay on it. Tests: `uv run --group dev pytest -q` → baseline **102 passed**
  (2 pre-existing starlette/httpx warnings).
- **Web/planning**: `~/Documents/Repos/pdf-splitter`, branch `feature/mvp`. It holds the PRD,
  Architecture, stories and findings. Read `docs/Architecture.md` § "Engine additions
  (monograph-splitter 0.4.0)", then `docs/stories/STORY-003.md` (its ACs are authoritative), then
  `docs/findings/STORY-002-findings.md` and `STORY-001-findings.md`. `docs/loop-state.json` belongs
  to the orchestrator, so never stage it.

Engine layout: `profile.py` (`Profile`, `load_profile`, `profile_from_dict`, `WEB_BASE`, `WEB_KEYS`,
`SINGLE_COLUMN`), `entries.py` (`entries_from_rows`, `select`), `session.py` (`Book`), `detect.py`
(new), `index.py`, `cuts.py`, `classify.py`, `render.py`, `verify.py`, `cli.py`, `diff_manifest.py`,
`review/server.py`. Tests build synthetic books with PyMuPDF (`tests/fixtures.py`). No test reads a
real book, and it should stay that way.

## What STORY-001/002 established (use these, don't re-invent)

- `profile.profile_from_dict(d, base=WEB_BASE) -> Profile`. Page = 1-based sheet (engine
  `sheet_offset=0`). The contract is `tests/test_profile_dict.py`.
- `Book.open(pdf=<path>, out=<dir>, profile=<Profile|path|name>, entries=<Path|str|list[dict]|EntryList>, log=…)`.
  The contract is `tests/test_entries_rows.py`.
- `detect.outline_levels(doc)`, `detect.outline_entries(doc, level)` and `detect.heading_candidates(doc, *,
  min_ratio, max_len, header_band, footer_band, wrap_gap, column_split, full_width_ratio)`
  (`src/monograph_splitter/detect.py`). They take an **open `fitz.Document`**, while `Book.open` takes a
  **path** and opens its own. The contract is `tests/test_detect.py`. In particular,
  `test_level_1_candidates_open_a_book_and_every_heading_is_located` is exactly the first half of your
  AC-3 e2e.
- `tests/fixtures.py:headed_book(path)` is the synthetic 2-column book AC-3 names. It has 3 chapters
  (p1 left, p3 full-width, **p4 mid-right-column**), 4 sections, a running header and page numbers,
  and an outline. Its return dict lists `chapters` / `sections` with page and col.

## Critical gotchas

1. **The CLI loop you move** is `cli.main` (`src/monograph_splitter/cli.py`, the `for entry in chosen:` block):
   it covers `select(book.entries, only, limit)`, `book.cut(entry, preview=, verify=, redact=not no_redact)`,
   `book.missing`, `book.save_manifest()`, `book.write_review_index(rows)` when previewing, then the
   flag/note counts and the log lines. AC-1's signature has no `redact`, but the CLI has
   `--no-redact`, so keep a `redact=True` kwarg, or the diff gate for `--no-redact` users silently changes.
   `--limit` also exists, so either `only` plus a limit, or selection in the CLI and handing
   `cut_all` the chosen entries. Pick one and keep the CLI output byte-identical.
2. **Entry names are used raw as filenames**: `Book.cut` writes `self.out / f"{entry.name}.pdf"`
   (and `excerpt_path`). Detected names can contain `/` (a real Maciocia candidate is a URL) and
   repeat ("Notes", "Introduction"). `Book.entry(name)`, `manifest` and `overrides.json` are keyed by
   name, so a duplicate overwrites. AC-3 uses the synthetic book's 3 unique, safe names, so it passes
   regardless. Do **not** change the CLI's filenames (the Inkwell diff gate and consumers read `file`).
   Record it for the web job layer, or add a web-only guard if it's trivial and CLI-neutral.
3. **The real-book diff gate (AC-2) must not write into Inkwell or the vault.** Inkwell's
   `docs/planning/curriculum/tools/extract_pattern_pdfs.py` writes to
   `~/Documents/Vaults/TCM_Knowledge_Base/Books/pattern-sources/` (the live vault) by default. Don't
   run it as-is. Instead, READ its `build_entries()` logic and `foundations_pattern_entries.json`, then
   build the entries JSON in a temp dir and run the engine CLI directly with
   `--pdf <Maciocia PDF> --profile maciocia-foundations --entries <tmp>/entries.json --out <tmp>/pre`
   (then `…/post`), once with the pre-change engine (`git stash` is not allowed: use a
   `git worktree add` of `ef828ab` in the scratchpad) and once with yours, both with `--verify`, then run
   `monograph-splitter-diff`. The PDF path is in the STORY-002 findings. Maciocia is 1319 sheets and
   the index takes a few minutes per run. Per-run index caches live in `--out`, so the temp dirs keep
   it isolated. When running from a path with `uv run --with <path>`, `touch pyproject.toml` first
   (stale-build cache).
4. **ENGINE_VERSION is an int (16) keyed into the index cache**, not a semver. There is no
   `__version__`. AC-4 reads "Version 0.4.0 in pyproject + ENGINE_VERSION", and the story note expects the bump
   (Inkwell re-indexes its books next run, which is expected; say so in the commit). No indexing rule
   changed in 001/002, so the bump is only a cache reset: it does not change any cut. Run both diff-gate
   runs on fresh `--out` dirs, so each builds its own index.
5. **The tag `v0.4.0`**: the AC says to push it to Gitea. Tag the `feature/web-mode` tip after your commit, and
   push the tag to `gitea` **and** `origin` (the repo is primarily GitHub now). Never push `main`.
6. `WEB_BASE.chapter_only` still marks a sheet whose line is a bare "Chapter N" (≥ 14 pt) as a chapter
   break. `headed_book` avoids it ("Chapter Two: The Middle of …"). Don't add a bare "Chapter 3"
   line to the fixture.
7. AC-3's "the mid-column chapter's first page has the previous chapter redacted": that is sheet 3
   (page 4), where Chapter 3 starts mid-right-column under Chapter 2's tail. In the written
   `Closing Chapter.pdf`, page 1 should keep only the heading and what follows it in the right
   column. Every body line in the fixture reads `body N of the running prose`, so text alone can't
   tell the two chapters apart. Assert on positions instead: `page_lines(out[0])` has no line
   left of `column_split·W`, and none above the heading's top outside the header and footer bands. Verify
   (`verify_headings`, since this is headings mode) must report no leaks (`row["leaks"] == []`, no `leak` flag).

## Recommended ordering

1. `session.Book.cut_all(progress=None, verify=True, preview=False, only=None, redact=True)`:
   the loop, `missing`, `save_manifest`, the review index when previewing, and the summary
   `{written, flags, leaks, missing}` (decide the shape: e.g. `written` = rows, `flags`/`leaks` =
   counts or per-entry lists. Pin it in a test, because the test is the contract for the web worker).
   `progress(done, total, name)` fires after each entry, including one that falls off the book.
2. `cli.main` on `cut_all`. The log output must stay identical (`tests/test_cli.py` covers some of it).
3. The synthetic diff gate: generate manifests for `scenario_book` + `tests/profile-test.toml` and
   `heading_book` + `tests/profile-headings.toml` through the CLI with `--verify`, before and after.
   `monograph-splitter-diff` must report 0 changed (STORY-001's findings describe exactly this).
4. The real-book diff gate (gotcha 3).
5. `tests/test_web_mode.py`: `headed_book` → `detect.heading_candidates` level 1 →
   `Book.open(entries=rows, profile=profile_from_dict({}))` → `cut_all(progress=…)`. Assert 3 PDFs,
   progress calls `(1,3,…),(2,3,…),(3,3,…)`, the p4 redaction, and 0 leaks.
6. Version 0.4.0 (`pyproject.toml`), `ENGINE_VERSION` 17, README (the web-mode section already
   documents `profile_from_dict`, `Book.open(entries=…)` and `detect`, so add `cut_all`), commit, tag, push.

## Conventions

- Python 3.11+. Match the surrounding style: a module docstring with a `# Docs:` header line,
  `from __future__ import annotations`, terse WHY comments and no WHAT comments.
- Commit on `feature/web-mode`:
  `feat: STORY-003 - Book.cut_all with progress; CLI on it; web mode documented (0.4.0)`, ending with
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Stage explicit paths only. Never `git add -A` / `.`, never `reset --hard` / `checkout .`.
- Push: `git push origin feature/web-mode`, `git push gitea feature/web-mode`, plus the tag.
- Findings and KICKOFF-STORY-004 go in `~/Documents/Repos/pdf-splitter` on `feature/mvp` as
  `docs: STORY-003 - findings + KICKOFF-STORY-004`, pushed to `origin` and `gitea`.

## Authority

- Free: the engine's `src/` and `tests/`, README, `pyproject.toml`, the version tag, and in the
  pdf-splitter repo `docs/findings/`, `docs/KICKOFF-*` and the STORY-003 status lines.
- Do not touch: `~/Documents/AI/Inkwell` (READ-ONLY: entries JSON and script logic only), the vault
  (the Maciocia PDF is read-only, and outputs go to temp dirs), the bundled profiles' values, `cuts`,
  `index`, `locate_heading`, `docs/loop-state.json`, PRD/Architecture (report doc errors in findings).

## Stopping conditions (BLOCKED protocol)

- A diff gate is non-zero and the cause is not a bug you can fix inside `cut_all`/CLI.
- An existing test fails and the cause isn't your change.
- You'd need credentials, cloud resources, money, or to write to Inkwell or the vault.

## Final report shape

Per-AC ✅/❌ with file:line, test counts (before 102 / after N), both diff-gate results (synthetic
labels + headings, and Maciocia), commits and tag (both repos), and anything STORY-004+ (the web
worker) should know, especially the `cut_all` summary shape and the name/filename issue.
