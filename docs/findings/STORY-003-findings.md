# Findings — STORY-003
**Date:** 2026-09-25
**Status:** done

Engine commit: `c8935d2` on `feature/web-mode` (`BigSpoon33/pdf-splitter-engine`), pushed to `origin` and `gitea`. Annotated tag **`v0.4.0`** (on `c8935d2`) is pushed to both remotes. An anonymous `git ls-remote` on GitHub sees the tag, and `uv run --no-project --with "monograph-splitter @ git+https://github.com/BigSpoon33/pdf-splitter-engine@v0.4.0"` installs and imports it (`__version__` 0.4.0, `ENGINE_VERSION` 17, PyMuPDF 1.28.2). `main` was not touched and is still `6fd22fc`.

## Gate r1 fix (0.4.1)

Round 1 of the review (`docs/findings/STORY-003-review.md`) confirmed one finding: `review/server.py`'s `excerpt_pdf` wrapped the lazy `cfg_of(book_id).book` (= `Book.open`) in the same `try/except ValueError` as the name guard, so a malformed `overrides.json` (or any other `ValueError` from opening the book) came back as 404 "no such excerpt" instead of surfacing as it did before 0.4.0.

Fixed forward in engine **`8e52cc3`** on `feature/web-mode` (`fix: STORY-003 - gate r1: the excerpt route maps only the name guard to 404 (0.4.1)`), pushed to `origin` and `gitea` with annotated tag **`v0.4.1`** on it. `v0.4.0` still points at `c8935d2` on both remotes and was not moved.

- `src/monograph_splitter/review/server.py:265`: `book = cfg_of(book_id).book` is resolved before the `try`; only `book.excerpt_path(name)` sits inside `except ValueError → 404`.
- Test `tests/test_review_server.py::test_the_excerpt_route_maps_only_the_name_guard_to_404`: an unsafe name (`a\b`, `..`) is 404; then `overrides.json` is corrupted, a fresh app is built, and its FIRST request is the excerpt route → `pytest.raises(json.JSONDecodeError)` (the same exception a pre-0.4.0 server raised); once the file is repaired the same app returns 404 for the unsafe name and 200 for the excerpt. The test fails against `c8935d2`'s route ("DID NOT RAISE"), so it guards the regression. The orchestrator's `scratchpad/gate/srv/probe.py` now prints the `JSONDecodeError` traceback instead of `404`.
- Version **0.4.1** in `pyproject.toml`, `uv.lock` and `__version__`; `tests/test_web_mode.py:190` pins `0.4.1`. `ENGINE_VERSION` stays 17 (no index change, so Inkwell does not re-index again). The README lists no version numbers, so it is unchanged.
- The tag is visible anonymously on GitHub (`git ls-remote --tags origin` shows `v0.4.1` → `8e52cc3`). The `uv run --no-project --with …@v0.4.1` install was NOT re-run in this session (the sandbox refused to execute code fetched from the external git source); the resolution path is identical to the `v0.4.0` check above, and STORY-004's `uv sync` is the next real test of it.
- Suite: **150 passed** (149 + 1), 2 pre-existing warnings.

## AC Verification
- [x] AC-1: `Book.cut_all(progress=None, verify=True, preview=False, only=None, *, limit=None, redact=True) -> dict`. The loop that was in `cli.main` now lives here: `select`, `cut` for each entry, `save_manifest`, and `write_review_index(rows)` when previewing. `progress(done, total, name)` fires after each entry, including an entry whose plan falls off the book. **Summary shape** (pinned by `tests/test_web_mode.py:33`, `SUMMARY_KEYS`):
  - `written`: the manifest rows cut in this call, in entry order.
  - `flags`: `{flag: count}`, the same dict the CLI prints.
  - `notes`: `{note: count}`.
  - `leaks`: `{name: row["leaks"]}` for rows flagged `leak`.
  - `missing`: `list(book.missing)`.
  - `unknown`: names in `only` that have no entry.
  
  The call does not make the manifest start over: it merges with earlier runs, like `--only`. Code: `src/monograph_splitter/session.py:228`. Tests: `tests/test_web_mode.py:33` (progress calls `(1,3,…),(2,3,…),(3,3,…)`), `:68` (a plan off the book still fires progress and lands in `missing`), `:83` (`only`, `limit`, `unknown`, manifest merge), `:96` (`redact=False`).
- [x] AC-2: `cli.main` calls `cut_all` (`src/monograph_splitter/cli.py:118`). `--limit` and `--no-redact` are passed through as `limit=` and `redact=`. The `--only: no entry for …` line is now logged after the cut instead of before it. No log line is emitted during the loop, so the log text is unchanged.
  - **Synthetic diff gate:** `scenario_book` + `tests/profile-test.toml` (labels) and `heading_book` + `tests/profile-headings.toml` (headings), each run 3 ways through the CLI: `--verify`, `--verify --no-redact`, and `--verify --limit 2 --only <3 names>`. The pre-change runs used a `git worktree` of `8c12b3f`; the post-change runs used the final tree. `monograph-splitter-diff` reports **0 changed** for all 6 runs. The `manifest.json` files are **byte-identical**, and the CLI logs are identical once the elapsed seconds are masked.
  - **Maciocia *Foundations* diff gate** (1319 sheets, read-only): the entries JSON was built with the logic of Inkwell's `extract_pattern_pdfs.build_entries()` (273 rows) into a scratch dir. The engine CLI then ran `--profile maciocia-foundations --verify` into fresh `pre/` (engine `8c12b3f`, index v16) and `post/` (engine `c8935d2`, index v17) dirs. Result: `monograph-splitter-diff` **0 of 211 entries changed**. The logs are identical apart from timing: 211 PDFs, 190 top / 193 bottom cuts, notes `{'uncut-banner-above': 19}`, no flags. The page indexes are equal. The only field that differs is `bytes`, on 4 rows by 1–6 bytes. This is MuPDF output nondeterminism, not a change: running `8c12b3f` twice also differs in `bytes` on 4 (other) rows. The extracted text of all 211 excerpts is identical. No `/` or `\` appears in any of the 273 names, so the new filename guard can't touch Inkwell.
- [x] AC-3: `tests/test_web_mode.py:33` covers the full chain: `headed_book` → `detect.heading_candidates(doc, min_ratio=1.25, profile=prof)` level 1 → `Book.open(entries=rows, profile=profile_from_dict({}))` → `cut_all(progress=…)`. It asserts:
  - Exactly 3 PDFs are written, with printed pages `[1,2] [3,4] [4,6]`.
  - The manifest file equals the sorted `written` rows.
  - `summary["leaks"] == {}`. Every row has `leaks == []` and no `leak`, `heading-not-found` or `long-span` flag.
  - On page 1 of `Closing Chapter.pdf`, every line between the header and footer bands sits right of `column_split·W` and at or below the heading's `y`. The first such line is "Closing Chapter".
  - Chapter 2's last page (the same sheet) does not contain the heading.
  - The `redact=False` test (`:96`) is the control: the unredacted page 1 still has the left column's `body 0 of the running prose`.
- [x] AC-4: `pyproject.toml` version is `0.4.0` (and so is `uv.lock`). `src/monograph_splitter/__init__.py:16` adds `__version__ = "0.4.0"` (new; use this one for `engine_version`). `:17` bumps `ENGINE_VERSION` 16 → 17; that value is the index-cache key. The commit message says Inkwell's books re-index once. The README's web mode section now also documents the new `WEB_BASE` defaults, `### Cutting every section (Book.cut_all)` (summary shape and the filename rule), and `heading_candidates(profile=…)` ("One profile for both calls"). The tag `v0.4.0` is pushed to Gitea and GitHub. Test: `tests/test_web_mode.py:184` checks that `__version__` equals the pyproject version.

### Orchestrator decisions (binding), implemented
- **Filename guard:** `session.safe_filename(name)` (`session.py:36`) raises `ValueError("entry name '<name>' can't be a file name …")` for an empty name, `.`, `..`, or any name containing `/`, `\` or NUL. Every other name passes through unchanged (`"..hidden"`, `"a.b"` and `"Chapter 1: Why?"` stay raw). The guard applies in three places:
  - `Book.excerpt_path`, which `Book.cut` now uses (`:184`, called before planning).
  - `cut_all`, which checks every chosen name **before the first write**: no PDF and no manifest is written.
  - The CLI, which turns the error into `Error: …` with rc 2.
  
  In the review server, `excerpt_pdf` maps the guard's error (and only that error, since 0.4.1 — see "Gate r1 fix") to a 404. Tests: `tests/test_web_mode.py:106` (6 names, including a `../escape` that must not land in the parent dir), `:124`, `:129`; `tests/test_review_server.py::test_the_excerpt_route_maps_only_the_name_guard_to_404`.
- **WEB_BASE:** `chapter_only = ""`, and `Profile.chapter_only_re` compiles an empty pattern to `(?!)`, which never matches, so empty *disables* the rule (`profile.py:140`). `long_span = 200 = max_span` (`profile.py:232-234`), and `profile_from_dict` moves `long_span` with `max_span` when the base has them equal (`:278`), the same trick `subheader_bottom` uses. `title_min_y` is unchanged. Tests: `test_web_mode.py:140` (a bare 16 pt "Chapter 3" line is a break under `Profile()` but not under `WEB_BASE`/`profile_from_dict({})`, and the empty pattern matches nothing) and `:151` (a 6-sheet section is not `long-span` under web defaults but is with `long_span=3`). `tests/test_profile_dict.py:37-42` now also asserts `long_span == max_span`.
- **One wrap-gap setting:** `detect.heading_candidates(doc, *, …, profile=None, header_band=None, footer_band=None, wrap_gap=None, column_split=None, full_width_ratio=None)` (`detect.py:195`). A profile supplies the bands, `column_split`, `full_width_ratio` and `wrap_gap` (= `heading_wrap_gap`). An explicit keyword still wins. With neither, the values are `Profile()`'s defaults, as before. Test: `test_web_mode.py:163`: `profile=` gives the same result as the explicit kwargs, `wrap_gap=16` overrides the profile's 24, no-profile gives the same result as `profile=Profile()`, and one profile for detect + `Book.open` gives 0 `heading-not-found` on the single-column book.

## Test Results
**Command:** `cd ~/Documents/Repos/monograph-splitter && uv run --group dev pytest -q`
**Result:** pass
```
149 passed, 2 warnings in 8.32s        (before: 133; +16 = tests/test_web_mode.py, 1 of them parametrized ×6)
150 passed, 2 warnings in 8.19s        (after the gate r1 fix, 8e52cc3: +1 in tests/test_review_server.py)
```
The 2 warnings are the pre-existing starlette/httpx deprecations.

## Bugs Found
- **Pre-existing, not fixed (`cuts` is off-limits):** an entry whose start page is **past the end of the book** crashes planning with `IndexError` (`cuts.plan`: `index[sheet0]`). The crash happens in `Book.planned`, so it kills `cut_all` and the preview for that entry. The existing "falls off the book" path (`in_range` → `missing`) is only reached through an override's `pages`. **The web layer must refuse `page ∉ [1, pages]` when it validates a Plan** (STORY-007 `PUT plan` → 422). Detection and outline rows are always in range.
- None in the new code.

## Handoff Context for Next Session
STORY-004 is the first code in the web repo. It only needs the dep pin: `monograph-splitter @ git+https://github.com/BigSpoon33/pdf-splitter-engine@v0.4.1` (the `v0.4.0` line was verified installable anonymously; `v0.4.1` is the same path one commit later). For health's `engine_version`, use `monograph_splitter.__version__` ("0.4.1"). `ENGINE_VERSION` (17) is the index-cache int. The web worker (STORY-006/007) passes ONE `profile_from_dict(settings)` to both `detect.heading_candidates(doc, profile=prof)` and `Book.open(profile=prof)`. It makes one `Book` per cut job, because `summary["missing"]` is `book.missing` and accumulates across calls on the same Book.

## Out-of-Scope Items
- **Unique and display names are the web job layer's job** (STORY-007). `cut_all` refuses unsafe names but does not dedupe: two entries with one name share a PDF, a manifest row and an override. The worker should pass engine names such as `NNN-<slug>` and keep the user's display names in its Plan.
- **Doc errors (Architecture, not edited):**
  - § Data Types says "output filenames are `NNN - <slug>.pdf`" and "the API dedupes by suffixing ` (2)`". The engine writes `<entry name>.pdf`, so those are the *engine entry names* the worker passes (with the display name kept separately), and the ` (2)` suffix is display-only.
  - The Plan's `overrides` are keyed by the name the engine sees, so the worker must translate display name → engine name when it calls `set_override`.
  - The Engine additions § signature `cut_all(progress, verify=True, preview=False) -> Summary` should gain `only`, `limit` and `redact`, and the summary shape above.
  - § File layout `work/<slug>.pdf` matches the `NNN-<slug>` scheme.
- Filename length: a 120-char UTF-8 name is ≤ 255 bytes only if it's mostly ASCII. `NNN-<slug>` with a slug capped at ~100 ASCII chars avoids `ENAMETOOLONG`.
- Follow-ups from STORY-002's gate (4+-line wrapped headings dropped, a size-scaled default `wrap_gap`, inline big glyphs) are unchanged. They belong to a detection-tuning story, or STORY-009's settings.
