# Findings — STORY-009
**Date:** 2026-09-25
**Status:** done

Commits: `784b31d feat: STORY-009 - source picker, editable section list and layout panel`, then the gate r1 fix
`9e29032 fix: STORY-009 - gate r1: overrides follow boundaries, undo/paste/picker state, saves survive leaving, badges
after reload` (feature/mvp, pushed to `origin` and `gitea`). § Gate r1 fixes cites lines as of `9e29032`; the AC
section below cites `784b31d` (its anchors moved by the fix — the function names still hold).

## Gate r1 fixes (`9e29032`)

`docs/findings/STORY-009-review.md` round 1: 6 confirmed, 3 refuted → 3 orchestrator additions. Every item has a
test that fails on `784b31d` (verified by copying the new test files into a `784b31d` worktree: the 32 new/changed
tests fail there; 147/147 pass on `9e29032`).

- **F2 overrides follow boundaries** — `web/src/lib/plan.ts:160` `dropEnd`: `removeSection` (:168) drops the END
  override (`endCut`/`endCol`) of section i−1, whose end moved to the next start (or the book's end); `insertSection`
  (:196) drops the end of the section before the slot, and the new section starts with no override. Start overrides
  of untouched sections stay; an override that was only an end disappears rather than leaving `{}`. Same rule
  `mergeWithNext` already used. Tests: `plan.test.ts` "removeSection: the section before the removed one keeps its
  start override and loses its end" / "insertSection: the new section starts clean and the one before it loses its
  end" (headed_book: sheets 1/3/4 of 6, overrides `{0: startCut 70 + endCut 300, 1: startCut 88 + endCut 520,
  2: startCut 400}`). Live: delete section 2 → `{"0":{"startCut":70},"1":{"startCut":400,"startCol":"right"}}`;
  add Interlude@2 → `{"0":{"startCut":70},"2":{…}}`.
- **F4 badges after reload** — `Review.svelte:55`: the manifest is requested whenever `Review` mounts (it mounts only
  in `review`/`done`), in parallel with the analysis and plan; a 409 `not_ready` (or any failure) means no badges and
  no error. The prop is now `jobState: 'review' | 'done'` (`JobPage.svelte:15`). The "cut again to refresh the files"
  note (`stale`, :90) shows whenever a manifest exists AND (the job is in `review` OR an edit happened since load,
  `PlanEditor.edited` `editor.svelte.ts:72`). Tests: `Review.test.ts` "a job back in review after an edit still shows
  the last cut badges and the stale note on reload (gate r1 F4)", "loads … 409 on the manifest is just 'no badges'".
  Live: rename from `done` → job `review`, badges kept + note; reload in `review` → badges kept + note.
- **F5 paste** — `SourcePicker.svelte:39-43`: "Paste a list" is a local VIEW (`local`/`mode`), not a pick: choosing it
  never calls `onpick`, and the box is `typed ?? formatList(sections)` — re-seeded from the CURRENT list on every
  switch (`choose`, :78, sets `typed = null`). Only "Use this list" (`useList`, :87) applies the text. The view yields
  to the saved source when it moves on (a pick elsewhere, an Undo). Tests: `SourcePicker.test.ts` "switching to the
  paste list changes nothing and always seeds the box from the current list (gate r1 F5)", "the paste view yields to
  the saved source…". Live: Paste → 0 PUTs, box = the current three lines; a bad line typed, Outline, Paste again →
  box re-seeded, the stale text gone, still 3 sections.
- **F6 undo scope** — `editor.svelte.ts` `UndoSnapshot` (:31): `replaceSections` snapshots `source`, `sections`,
  `overrides` and the picker state only; `undoLast` (:140) restores those and leaves `settings` as they are. Tests:
  `editor.test.ts` "Undo restores the source, list, overrides and picker controls — never a setting changed since",
  `Review.test.ts` "Undo puts the picker controls back and keeps a setting changed after the switch". Live: level 2,
  header band 60, Undo → the three level-1 names, header band still 60 (`GET /plan` header_band 60).
- **F7 no lost saves** — `destroy()` (`editor.svelte.ts:293`) commits a name draft and calls `flush()` without
  awaiting (a request already out queues one more, as before). `pagehide` and `visibilitychange → hidden` on the
  window (`UnloadSource`, :21; listeners bound in the constructor, removed by `destroy`) call `sendBeforeUnload`
  (:257): an UNSENT edit is sent at once with `{keepalive: true}`, bypassing the one-in-flight rule (the older request
  left first, and nothing may run after unload); nothing unsent = nothing sent; a `gone` job sends nothing.
  `putPlan(id, plan, {signal?, keepalive?})` (`api.ts:242`) passes it to `fetch`. Tests: `editor.test.ts` "PlanEditor
  never loses a save (gate r1 F7)" ×5, `Review.test.ts` "sends a pending edit when the page is left rather than
  dropping it". Live: page 5 + navigate to `/` at once → `GET /plan` pages `[1,2,5]`; page 4 + the tab hidden → the
  PUT left immediately with `[1,2,4]`.
- **F8 picker resync** — the controls' state moved into the editor: `PickerState` (`plan.ts:31`: `outlineLevel`,
  `headingLevel`, `threshold`, `maxLength`), `initialPicker(analysis)` (:41), `PlanEditor.picker`
  (`editor.svelte.ts:66`). `SourcePicker` has no local level/threshold state: it renders `picker` and reports the
  next state as `onpick`'s 4th argument; `replaceSections(source, sections, label, picker)` stores it with the list
  and Undo restores it, so the `<select>`/slider show the restored values and choosing the previous option is a
  change event again. Tests: `SourcePicker.test.ts` "shows the restored controls after an Undo, and choosing the old
  option again re-applies it", `Review.test.ts` (F6/F8). Live: Undo → select shows 1; level 2 again → 3 level-2
  sections.
- **Name drafts (addition)** — `PlanEditor.draft` (:83), `setDraft(i, name)` (:159, on focus/input), `commitDraft()`
  (:164, on blur/Enter/`destroy`/unload; a same name is not an edit). `SectionList.svelte:26` `nameOf` shows the draft
  while it exists, so a 200 that lands mid-typing never rewrites the focused input; the normalized name appears after
  the commit. Tests: `SectionList.test.ts` "keeps a typed name as a draft and commits it on blur", "Enter commits the
  draft"; `editor.test.ts` "PlanEditor name drafts". Live: "Front matter " typed, 1.5 s → no PUT, plan unchanged;
  Enter → PUT, the input then shows the trimmed "Front matter".
- **Merge badge (addition)** — the manifest rows now live in `PlanEditor.rows` (:68; `Review` fills them,
  `SectionList` reads `editor.rows`); `merge(i)` (:185) drops the row with `index === i` — the merged section's row
  described the old span. Tests: `SectionList.test.ts` "a merge drops the badge of the section that absorbed the next
  one", `editor.test.ts` "PlanEditor manifest rows". Live: Merge ↓ on 1 → no badges.
- **PRD scope-2 heading filters (addition)** — `headingSections(analysis, {level, threshold, maxLength?, bands?})`
  (`plan.ts:64`): candidates longer than `maxLength` (default `MAX_HEADING_LENGTH` = 90, the analysis `max_len`) or
  whose top `y` sits inside the CURRENT `header_band` / `H − footer_band` (`analysis.size[page-1].H`) are out — the
  engine's own `heading_candidates` rule, applied to candidates detected with the default bands. `SourcePicker` has a
  "Max heading length (characters)" input (applies live like the slider) and takes `settings` for the bands (from
  `editor.plan.settings`, so a LayoutPanel band edit updates the count at once); when the count no longer matches the
  list a "Use these headings" button re-applies (`headingsDiffer`, `SourcePicker.svelte:55`). Tests: `plan.test.ts`
  "drops candidates longer than maxLength and those inside the current bands", `SourcePicker.test.ts` "headings: a max
  length and the current bands narrow the count…". Live (headed_book: chapter tops at y 72.9, Closing Chapter at
  382.9): max length 20 → 1 section (Closing Chapter); header band 100 → count 1 of a 3-section list, "Use these
  headings" shown → click → [Closing Chapter], button gone.

Counts after the fix: `bun run test` **147 pass** (11 files; was 127), `bun run check` 0 errors 0 warnings (316
files), `bun run build` 77.34 kB JS (28.50 gzip; was 74.04 / 27.54), `uv run pytest` 335 (unchanged), ruff clean.
Manual run as in attempt 1 (API 8010 + worker on a scratch jobs dir, `API_PORT=8010 bun run dev --port 5179`,
headless Chromium 1.61 via the Browser skill's playwright, all stopped by PID; `document.title` "PDF Splitter"
throughout; raw job id in api.log/worker.log: 0; console: only the browser's own "Failed to load resource: 409" for
the manifest before a cut — the same class of line as attempt 1's 422 ones, not an app error).

## AC Verification
- [x] AC-1: `web/src/components/SourcePicker.svelte` — three radios (`Outline` / `Headings` / `Paste a list`). Outline:
  a `<select>` with one option per `analysis.outline.levels[i]` reading `Level n — count items: three sample titles`
  (`samples`, :44; never assumes level 1 = chapters — on Maciocia L1 is 23 front-matter items, L2 the 339 chapters),
  disabled with "This PDF has no outline (bookmarks)…" when every level is 0 (`hasOutline`, :29). Headings: a range
  slider in × body size (`MIN_THRESHOLD` 1 → `thresholdMax`, `web/src/lib/plan.ts:37`), a level select (`Any level` +
  `Level n — size pt, count found`), a live count (`data-testid=heading-count`); disabled with an explanation when
  no candidate was found. Paste: a textarea parsed live by `parseList` (`plan.ts:76`, `Name, page` per line, the LAST
  comma or a tab splits), every bad line listed as `Line n: <why> — <text>` (`#paste-errors`), "Use this list (n)"
  enabled only for a clean, non-empty list. The initial choices come from `analysis.suggested`
  (`initialOutlineLevel` `plan.ts:44`, `initialHeadingLevel` :50). Sections built by `outlineSections` (`plan.ts:21`)
  / `headingSections` (:29; `size ≥ threshold × body_size`, level 0 = any).
- [x] AC-2: every picker change calls `onpick(source, sections, label)` → `PlanEditor.replaceSections`
  (`web/src/lib/editor.svelte.ts:73`): source + list + `overrides: {}` replaced, a snapshot kept for Undo (a run of
  picks — a slider drag — keeps the FIRST snapshot), the toast in `Review.svelte` ("Section list replaced from …",
  `Undo` button → `undoLast` :84) for `UNDO_MS` (8 s, `web/src/lib/config.ts`). Persistence: `touch()` → `flush()`
  (`editor.svelte.ts:160`) after `SAVE_DEBOUNCE_MS` = 600 ms (`config.ts`); one request in flight, a change made
  meanwhile queues exactly one more PUT with the latest plan; the 200 body replaces the local plan only when no edit
  happened while the request was out (`version`, :195). `putPlan` (`web/src/lib/api.ts:236`) through the shared
  `request()`. Tests: `editor.test.ts` "PUTs 600 ms after the last edit, once for a burst", "keeps one request in
  flight and sends the latest plan after it", "adopts the 200 body as the new truth"; `Review.test.ts` "switching
  source replaces the list with an Undo toast; Undo restores it; both persist".
- [x] AC-3: `web/src/components/SectionList.svelte` — per row: name `<input>` (`rename`, `editor.svelte.ts:93`),
  page `<input type=number>` in sheets (ADR-003) with `p. <label>` beside it when `analysis.pageLabels[page-1]` is
  non-empty (`label`, :23), `Merge ↓` (`merge` :114 → `mergeWithNext` `plan.ts:127`: i keeps its start, takes i+1's end
  override, later keys shift), `Delete` (`remove` :107 → `removeSection` `plan.ts:115`), an add form (name + sheet,
  `add` :122 → `insertSection` `plan.ts:142`, inserted in book order; `novalidate` so the range message is inline).
  Badges: `rowFor(rows, plan, i)` (`plan.ts:182`, a row counts only while index AND name still match, so an edit drops
  a stale badge) → `badgeFlags` (:173, `span-clamped` dropped on the last section, STORY-007 findings § Out-of-Scope)
  → `flagLabel` (:165). The rows come from `getManifest` (`api.ts:245`) when the job is `done` (`Review.svelte:73`).
- [x] AC-4: `web/src/components/LayoutPanel.svelte` — `One column`/`Two columns` radios (`single_column`), gutter as a
  % number input (`column_split × 100`, 20–80, disabled for one column; :74), header/footer band (0–200 pt), heading
  size (`heading_min_size`, 4–72), and the orchestrator's wrap gap (`heading_wrap_gap`, 0–200, shows the engine's 16
  until set; :78). Each through `setSetting` (`editor.svelte.ts:130`); a blank field is ignored (never sends NaN).
- [x] AC-5: a 422's `errors[].loc` is indexed by `locKey` (`editor.svelte.ts:19`, `["body","sections",0,"page"]` →
  `sections.0.page`; Pydantic's `Value error, ` prefix stripped, :25). Controls ask `editor.errorAt(...)` (:141) and
  render `aria-invalid` + an `aria-describedby` `<p class="field-error">` right under the input (SectionList name/page,
  every LayoutPanel field); `sections`/`overrides`/`source` errors render at the list/picker level (`listErrors`, :146).
  Cleared by the next 200. Tests: `editor.test.ts` "keeps 422 field errors by loc…", `Review.test.ts` "renders a 422
  next to the offending control and clears it on the next success".
- [x] AC-6: `web/src/lib/plan.test.ts` (parser good/bad lines with line numbers, sources, merge/delete/insert re-keying,
  badges), `web/src/lib/editor.test.ts` (debounce, in-flight coalescing, 422/409/410/network, undo + expiry),
  `web/src/components/{Review,SourcePicker,SectionList,LayoutPanel}.test.ts` (source switch + undo through the DOM,
  outline disabled + explanation, live count, per-line errors, rename/page/label/delete/merge/add, badges, layout
  fields), `api.test.ts` (getAnalysis/getPlan/putPlan/getManifest). All loaders are injected props or a stubbed
  `fetch`: no network, no API.
- [x] Orchestrator addendum — manifest route: `src/pdf_splitter/routes/download.py:44` `get_manifest`: `load_job`
  (404/410) → `_result_zip` (409 `not_ready` before any cut) → the ZIP's `manifest.json` as the JSON list (rows carry
  the plan `index`; kept after a PUT returns the job to `review`, until the next cut replaces the zip). No new code
  or log line (the access log already hashes ids). Tests: `tests/test_api_e2e.py::test_manifest_serves_the_last_cuts_rows_from_the_zip`
  (409 → 200 → still 200 after PUT/review → 410 after DELETE → 404 unknown; no id in the logs) and
  `::test_manifest_from_a_real_cut_matches_the_zip` (the real sandboxed cut task; one row per plan section, in order).
- [x] Orchestrator addendum — wrap gap: `PlanSettings.heading_wrap_gap: float | None` (`src/pdf_splitter/models.py:44`,
  0–200; the engine only requires ≥ 0) and `PlanSettings.dump()` (:46) which leaves the key out when unset, used by
  `Plan.dump` and the section-plan preview request (`routes/preview.py:133`). So an untouched plan keeps exactly the
  five keys every existing test pins (`tests/test_worker.py::test_default_plan_settings_round_trip_and_match_the_index_profile`,
  the e2e `r.json() == plan`), and a set gap reaches `profile_from_dict` for previews and the cut. Test:
  `::test_put_plan_keeps_a_heading_wrap_gap_only_when_sent` (absent → 5 keys; 30 → saved, returned, engine hash
  changes; -1 / 200.5 → 422 `["body","settings","heading_wrap_gap"]`; a preview under it → 200).

## Test Results
**Command:** `cd web && bun run check && bun run test && bun run build`
**Result:** pass
```
svelte-check: COMPLETED 316 FILES 0 ERRORS 0 WARNINGS 0 FILES_WITH_PROBLEMS
vitest:       Test Files  11 passed (11) · Tests  127 passed (127)        (before: 68 in 5 files)
vite build:   dist/index.html 0.53 kB · index-*.css 7.31 kB (gzip 2.10) · index-*.js 74.04 kB (gzip 27.54)   (before 44.12 / 17.39)
```
**Command:** `uv run pytest -q && uv run ruff check` → `335 passed in 77.08s` (before 332; +3 in `tests/test_api_e2e.py`) ·
`All checks passed!`

**Manual run** (API `--port 8010` + worker on a scratch `PDFSPLIT_JOBS_DIR`, `API_PORT=8010 bun run dev --port 5179`,
headless Chromium 1.61 through the Browser skill's playwright — not a repo dependency; all three stopped by PID):
```
b.pdf (headed_book, 6 sheets)
  3.7s review · sections [1 Foundations…, 2 Chapter Two…, 3 Closing Chapter] pages [1,3,4] · Outline checked
       level options: "Level 1 — 3 items: 1 Foundations of Testing · 2 Chapter Two: The Middle… · 3 Closing Chapter" / "Level 2 — 3 items: …"
  3.9s click Headings → [Foundations of Testing, Chapter Two…, Closing Chapter] · toast "Section list replaced from the detected headings. Undo"
  4.7s PUT 200 source=headings · status "Saved" · click Undo → the three outline names back, toast gone, Outline checked · PUT 200 source=outline
  5.0s rename "Intro" then "Introduction" 300 ms apart → ONE PUT, body name "Introduction" · "Saved"
  6.0s page 99 on section 2 → PUT 422 · that input aria-invalid=true, message beside it "page must be at most 6, the last page of the book"
       (section 1's page input aria-invalid=false) · status "Unsaved changes" · page 3 → PUT 200, aria-invalid=false
  7.9s one column + header band 60 + wrap gap 30 → PUT 200 settings {…,"single_column":true,"header_band":60,"heading_wrap_gap":30} · gutter disabled
  8.9s two columns + gutter 90 % → PUT 422, gutter aria-invalid=true "Input should be less than or equal to 0.8" · 55 % → 200
 10.1s RELOAD → [Introduction, …] header band 60, wrap gap 30, gutter 55, "Saved" · GET /plan: the same (column_split 0.55, heading_wrap_gap 30)
 11.0s Paste a list → textarea prefilled "Introduction, 1\n2 Chapter Two…, 3\n3 Closing Chapter, 4", PUT source=manual
       "Front, 1 / not a line / End, 9 / OK, 5" → errors "Line 2: Expected "Name, page" — not a line", "Line 3: Page 9 is past the last page (6) — End, 9", Use disabled
       "Front, 1 / Middle, 3 / End, 5" + Use this list → [Front, Middle, End] PUT 200 manual · add Interlude@2 → [Front, Interlude, Middle, End]
       Merge ↓ on 1 → [Front, Middle, End] · select 2 + Delete → [Front, End] PUT 200 "Saved"
Maciocia (1319 sheets, 56.7 MB copy)
 45.6s review (31.7 s) · 23 sections [Front cover, Half title page, …]
       level options: "Level 1 — 23 items: Front cover · Half title page · The Foundations of Chinese…" / "Level 2 — 339 items: Notes · Notes · Introduction" / "Level 3 — 449 items: Historical Development · …"
 47.2s level 2 → 339 sections, names as the server normalized them ("Notes", "Notes (2)", "Introduction") · PUT 200 · Undo → 23 · PUT 200
 48.8s POST /cut (API, STORY-011 owns the button) 202 → done 23/23 at 63.9s · GET /manifest 23 rows, flags e.g. 0:end-at-known-start+heading-not-found … 22:span-clamped
 64.2s RELOAD → "Sections ready", badges on 20 rows ("Ends where the next section starts", "Heading not found on its page", "Reached the page limit", "May be cut short"); row 22 (last, span-clamped only) has NONE
 65.1s rename section 6 from done → PUT 200, job state review, that row's badges gone (stale), "Saved"
document.title "PDF Splitter" throughout · console: Vite HMR + the browser's own "Failed to load resource … 422" lines only
api.log 92 lines / worker.log: raw job id occurrences for all 5 jobs: 0
```
Maciocia's `pageLabels` are all `""` (STORY-006/007: PyMuPDF can't parse its label tree), so the `p. <label>` hint
was exercised by `SectionList.test.ts` "changes the page and shows the printed label when the PDF has one" only.

## Bugs Found
- none in the API. Two things surfaced while wiring the UI and were handled in `web/`: (1) Pydantic's field messages
  begin with `Value error, ` for custom validators (`page must be at most …`, override bounds) — stripped client-side
  (`editor.svelte.ts:25`); (2) the browser's own constraint validation (`max=`) would swallow the add form's submit
  before our inline message — `novalidate` on that form.

## Decisions (small ambiguities resolved)
- **Manifest rows are the ZIP's rows verbatim** (`index`, `name`, `file`, `flags`, `notes`, `leaks`, `bytes` plus the
  engine's own keys such as `printedPages`, `pageCount`); the Architecture line lists `pages`, the engine writes
  `printedPages` — the SPA types only what it reads (`ManifestRow`, `api.ts:217`) and matches rows by `index` + `name`.
- **Wrap gap = an optional `PlanSettings` key, saved only when sent.** The addendum wanted it in the saved settings;
  `PlanSettings` is `extra="forbid"`, so this is the one backend change beyond the manifest route (`models.py:44`).
  Architecture § Data Types' Plan line does not list it yet (doc gap below). A set gap changes the profile hash, so
  the first preview / the cut re-index (12–14 s on Maciocia) — same as any layout change (STORY-007 findings).
- **The picker's detail controls follow the SAVED source** (`source={editor.plan.source}`): the level select shows
  under Outline only while Outline is the plan's source, etc. *Paste a list* is the exception since gate r1 (F5): it
  is a local view seeded from the current list (`formatList`) that changes nothing until "Use this list".
- **Slider + level:** both filter (`size ≥ threshold × body_size` AND the level when one is chosen); the initial
  threshold is 1.0 so the suggested level reproduces the default plan. Slider top = the biggest level in body sizes,
  at least 2.
- **Undo snapshot per run:** a slider drag fires `replaceSections` per step; the first snapshot is kept while the
  toast is up so Undo returns to the list before the drag, not to the previous tick.
- **A response is adopted only when no edit happened meanwhile** (`version`): the server's normalized names for an
  OLD text must not clobber what was typed since; the queued follow-up PUT carries the latest and its answer is adopted.
- **From `done`, an edit's PUT returns the job to `review` server-side**; `JobStatus` keeps its "Sections ready"
  card (it does not re-poll), so `Review.svelte` says "Your edits replace the plan of the last cut; cut again to
  refresh the files." while dirty. Badges of an edited row disappear (index+name no longer match).
- **Selection** lives in `PlanEditor.selected` (`editor.svelte.ts:49`, a radio per row, `select()`), kept in step with
  delete/merge/add so STORY-010's preview can bind to it.
- **No new dependencies.** `web/src/lib/fixtures.ts` is test-only (imported by `*.test.ts`, never by app code).

## Handoff Context for Next Session
`Review.svelte` owns one `PlanEditor` per job (`editor.plan` is the local truth, `editor.selected` the section to
preview, `editor.setSetting('column_split', …)` is how a gutter drag lands, and overrides go through
`editor.plan.overrides[String(i)]` + a `touch`-style method you add — none exists yet since nothing in STORY-009
edits an override). Since gate r1 the editor also owns `picker` (the SourcePicker controls), `rows` (the last cut's
manifest), `draft` (a name being typed) and `edited`; it listens to `pagehide`/`visibilitychange` itself and
`destroy()` sends what is pending — a component that unmounts the editor must call `destroy()`, never just drop it.
`Review` takes `jobState: 'review' | 'done'`. Preview endpoints are unchanged from STORY-007 and their contracts are
the tests (`::test_section_plan_returns_the_engine_view_with_rects`,
`::test_sheet_png_renders_through_the_sandboxed_subprocess_and_caches`, the 410-during-render tests at
`tests/test_api_e2e.py:487-580`).

## Out-of-Scope Items
- **Re-detecting headings under a user wrap gap** (orchestrator addendum): candidates are computed once at analysis
  time with the engine's default 16; the LayoutPanel gap feeds only the saved settings (previews + cut). A
  `POST /api/jobs/{id}/detect {settings}` (or a re-analyze) would be a new route — a follow-up.
- **Doc gap (Architecture, not changed):** § Data Types' Plan `settings` should list the optional `heading_wrap_gap`;
  § API Interface's manifest line says `pages` where the engine's rows carry `printedPages`/`pageCount`.
- **`JobStatus` does not resume polling** after a cut is queued from the review page (STORY-011's cut button will
  need it: either remount `JobStatus` on the cut or let it accept a "poll again" signal).
- The picker cannot show which outline level / threshold a reloaded plan came from (the Plan stores only `source`;
  `PlanEditor.picker` lives in memory); it starts from `initialPicker(analysis)` (`analysis.suggested`, threshold 1,
  max length 90). Persisting the picker state would need a Plan field (Architecture § Data Types) — not scheduled.
- A LayoutPanel band edit updates the headings COUNT, not the list: re-applying is the "Use these headings" button
  (a layout edit silently replacing the section list would be worse). The bands filter runs on candidates detected
  with the default bands, so a band SMALLER than the default cannot reveal more — that is the re-detect follow-up.
- Cut/download/delete/expiry UX (STORY-011); `queue_position`/rate limits (STORY-012); Caddy (STORY-013).
