# Findings — STORY-011
**Date:** 2026-09-25
**Status:** done

Commit: `68769d3 feat: STORY-011 - split, download, delete-now and expiry UX` (feature/mvp, pushed to `origin` and
`gitea`). Web + README § Web only; nothing in `src/pdf_splitter/`, `tests/` or the engine changed. Lines below are
as of that commit. **Gate r1 failed (3 confirmed, `STORY-011-review.md`) → fixed forward in
`fix: STORY-011 - gate r1: server decides expiry, results never outlive a failed refresh, one split per click`
(see § Gate r1 fixes at the end; its lines are as of that commit).**

## AC Verification
- [x] AC-1: Split posts `/cut`, shows progress per section, then the results list (per-section links + flags) and
  "Download all (ZIP)" — `web/src/components/Download.svelte:34` `split()` (commits a name draft, `editor.flush()`,
  refuses to cut a plan the API did not accept, then `postCut`), `:78` "Cutting section N of M…" + bar from the same
  status the poll renders, `:86` ZIP link, `:88` per-row `<a href={sectionUrl(id, row.index)} download>` + size +
  `flagLabel` badges (`badgeFlags`, so the last file's `span-clamped` is not shown). The poll resumes via
  `JobStatus`'s new `resume` prop (`JobStatus.svelte:30`, option (b) of the kickoff: the same instance restarts its
  loop, `Review` is never remounted). `JobPage.svelte:41` `onstatus` recognises the `done` that ends a cut
  (`cutPending`) and bumps `cuts`; `Review.svelte:133` fetches the new manifest on each → `results` + `editor.rows`.
- [x] AC-2: editing after a cut → status `review`, downloads stay — `Review.svelte:157`: when `editor.saves`
  grows while the page's `jobState` is `done`, `onsaved()` → `JobPage` bumps `resume` → ONE poll reads the server's
  `review` (terminal, the loop stops again). **Decision:** poll once after a save rather than flip the state
  locally — the status card shows the API's truth (`put_plan` does the `done → review` transition). The results list
  is kept (`results` is separate from `editor.rows`, which a merge filters) and titled "Files from the last cut"
  while stale; hidden only while the next cut runs (the ZIP is being replaced).
- [x] AC-3: "Delete now" with `confirm()` → `DELETE` → deleted screen; expiry always visible —
  `web/src/components/Expiry.svelte:48` `deleteNow()` (injectable `confirmDelete`/`remove`; a 404/410 from the DELETE
  also counts as deleted), `:66` "Files deleted in 23 h." from `expires_at` (`lib/expiry.ts:timeLeft`, floored hours,
  then minutes), re-read once a minute (`EXPIRY_TICK_MS`, `lib/config.ts`). Mounted by `JobPage` as soon as the
  first status arrives (so during analysis too).
- [x] AC-4: 404/410/expired → the whole page is the deleted screen with "Split another PDF" —
  `web/src/components/JobPage.svelte:57` `goneWith()` is the single signal (first reason wins), fed by `JobStatus`
  `ongone` (poll 404/410), `Review` `ongone` (`editor.gone` from a save or preview, a 410 on the cut or manifest),
  `Expiry` (`ondeleted`, `onexpired` when the clock passes `expires_at`). `{#if gone}` replaces JobStatus, Review
  and the editor's error line with `Expired.svelte` (reasons `deleted` / `expired` / `not_found`, heading focused).
- [x] AC-5: footer links to `/privacy` and `/terms` — `web/src/App.svelte:38`; routes `web/src/lib/route.ts:13-14`;
  placeholder pages `components/Privacy.svelte`, `Terms.svelte` (only facts ADR-007 already guarantees; STORY-014
  writes the text). `linkClick()` (`route.ts`) is the shared in-app link handler (App's local `home()` is gone).

## Test Results
**Command:** `cd web && bun run check && bun run test && bun run build`
**Result:** pass
```
svelte-check: COMPLETED 330 FILES 0 ERRORS 0 WARNINGS
Test Files  17 passed (17)
     Tests  228 passed (228)      (before: 13 files, 190)
dist/assets/index-*.js   101.24 kB │ gzip: 36.26 kB   (before ≈ 92 kB / 33.3 kB)
```
**Command:** `uv run pytest -q && uv run ruff check` (unchanged side, run as the baseline): `344 passed`, `All checks passed!`

New tests: `components/Download.test.ts` (7), `components/JobPage.test.ts` (8 — a fake API following the
transitions `tests/test_api_e2e.py` pins: split → done → results; edit → review with downloads kept; reload in
done; delete confirm/cancel; 404/410 at load; 410 mid-session over the editor error; past `expires_at`),
`components/Expiry.test.ts` (3), `lib/expiry.test.ts` (10), plus `api.test.ts` (+4: `postCut`, `deleteJob`,
`resultUrl`, `sectionUrl`), `route.test.ts` (+4), `JobStatus.test.ts` (+2: resume after a terminal state, `ongone`).
`Review.test.ts`'s `badges()` now excludes the results list (the same flag appears on the file row).

**Manual run** (API :8010 + worker + Vite :5181, headless Chromium via Playwright with `/usr/bin/chromium`):
- `headed_book`: Split into 3 PDFs → done in ~1.7 s → 3 rows; a section PDF (20,793 B, `%PDF-`) and
  `b-sections.zip` (4 entries incl. `manifest.json`) downloaded through the proxy; edit → `data-state=review`,
  "Files from the last cut", ZIP link kept; footer → `/privacy`, `/terms`; Delete now (confirm text shown, accepted)
  → "Your files were deleted"; reload of the job URL → "This job was deleted".
- Maciocia (1,319 pages, 56 MB): analysis ready at 29 s, "Split into 23 PDFs", cut ≈ 15 s with 7 distinct
  "Cutting section N of 23…" lines, 23 rows (20 with badges), a section download (155,775 B) and
  `maciocia-sections.zip` (24 entries) through the proxy; then the same edit / footer / delete / stale-URL steps
  all passed. `document.title` stays "PDF Splitter" (no id).

## Bugs Found
none

## Handoff Context for Next Session
`JobPage` owns the job-level state (`job`, `gone`, `resume`, `jobState`, `cuts`) and passes injectable loaders down
(`review`/`expiry` props) — reuse that seam for a ranges-mode page rather than a second page component. `Review`
mounts `Download` itself (it owns the `PlanEditor`), so a ranges review needs `Download` mounted next to its range
editor with the same props.

## Out-of-Scope Items
- **A failed cut is a dead end.** A cut that fails leaves the row `failed`/`kind: cut`, outside `EDITABLE`
  (`routes/common.py:16`), so `PUT /plan` and `POST /cut` answer 409 `not_ready`. The SPA disables Split and says
  "Upload the PDF again to retry". Letting a failed cut return to `review` is an API/Architecture (§ Job states)
  decision — not taken here.
- **Edits during a running cut** get 409 `busy` (the editor shows its message + Retry); when the cut finishes, a
  `busy` edit is re-sent automatically (`Review.svelte`, cut-finished effect). No UI lock on the editor while cutting.
- **Doc note:** STORY-011's kickoff named the next kickoff `KICKOFF-STORY-012`; the orchestrator re-sequenced to
  STORY-015 (ADR-009), so `docs/KICKOFF-STORY-015.md` is the one written.

## Gate r1 fixes (2026-09-25)

Review: `docs/findings/STORY-011-review.md` round 1 — 3 confirmed. One commit on the feature/mvp tip,
`fix: STORY-011 - gate r1: server decides expiry, results never outlive a failed refresh, one split per click`.
Each fix has tests that fail on `68769d3` (run there in a scratch worktree: the 10 gate tests below fail —
`Expiry.test.ts` and `lib/expiry.test.ts` fail wholesale there because the props/signature changed;
`tests/test_api_e2e.py` `-k "status_shape or seconds_left"` → `set(body) == STATUS_KEYS` AssertionError +
`KeyError: 'seconds_left'`).

1. **Expiry is the server's call (F1).** `GET /api/jobs/{id}` now carries `seconds_left`
   (`src/pdf_splitter/routes/plan.py:30` `seconds_left(job, now)` — whole seconds until `expires_at` by the server
   clock, clamped at 0; `:50` in `status_of`; Architecture § API Interface + README updated). Contract:
   `tests/test_api_e2e.py::test_job_status_shape_hides_the_requeue_marker_and_shows_failures` (`STATUS_KEYS`, range
   check) and `::test_seconds_left_counts_down_on_the_servers_clock` (:159 — a row created 23 h 30 min ago answers
   ≈ 30 min, equal to the server-side difference; 0 at and past the deadline).
   SPA: `web/src/lib/api.ts:26` `JobStatus.seconds_left`; `JobPage.svelte:23` `receivedAt = performance.now()` at
   every status (`:46`); `Expiry.svelte:10-12` takes `secondsLeft` + `receivedAt` and counts down on the monotonic
   clock (`:47`, `now` injectable, `Date` is never read — `lib/expiry.ts:5` `timeLeft(ms)` takes a duration and
   reads "less than a minute" at/past zero instead of null). When the count runs out (+ `EXPIRY_GRACE_MS` 1.5 s,
   `config.ts:31`, so the server's whole-second deadline has passed) `onexpired` → `JobPage.svelte:78` bumps
   `resume` = ONE more poll; a 200 re-arms the countdown from the fresh count, and ONLY the poll's 404/410 (via
   `JobStatus` `ongone`) shows the deleted screen. `Expiry` no longer has an `onexpired → gone` path.
   Tests: `Expiry.test.ts` (count → poll once, no deleted screen; a fresh status re-arms; `vi.setSystemTime` ±25 h
   changes nothing), `JobPage.test.ts` (`it.each([25, -25])` fake `Date` off by 25 h → "Files deleted in 23 h.",
   page stays while the API answers 200; `seconds_left: 0` → second poll 200/3600 → "1 h", exactly 2 polls, no
   deleted screen; second poll 410 → deleted screen).
   Live (`rv011-retry/skew.py`, Playwright clock +25 h / −25 h / +2 h): "Files deleted in 23 h." every time, only
   200s seen. `zero.py` (server deadline moved to +20 s in `jobs.db`): "less than a minute" → poll at 19.7 s (200,
   count 0 → re-armed) → poll at 21.2 s → 410 → "This job was deleted" (reason `expired`).
2. **No stale results (F2).** `Review.svelte:172` empties `results` the moment a cut finishes (`cuts` bump), then
   `loadResults()` (`:138`) fetches the manifest; a non-gone failure sets `resultsError` (`:162`) and schedules ONE
   automatic retry after `MANIFEST_RETRY_MS` 2 s (`:163`, `retryMs` prop for tests); the error stays on screen
   while a retry is in flight (`refreshing` → `Download` `retrying`, button reads "Retrying…"). `Download.svelte:114`
   renders `resultsError` as an inline alert with Retry (`onretry` → `Review.svelte:255`) in place of any list — the
   previous cut's rows are never shown since their links now point at other files. A 404/410 still goes to `ongone`.
   Tests: `Review.test.ts` (502 twice: rows gone at once, automatic retry, Retry button → new rows; 4 manifest
   calls), `JobPage.test.ts` (re-cut with the manifest answering 502 once: alert + Retry, no ZIP link, none of cut
   1's files, automatic retry lists cut 2's files, 4 manifest calls), `Download.test.ts` (error + Retry rendering).
   Live (`mfail2.py`, route `**/manifest` → 502): after cut 2 "Sections ready", 0 rows, 0 ZIP links, alert "The
   list of your files could not be loaded. Something went wrong on our side. Try again." + Retry; the automatic
   retry (or Retry after a persistent 502) lists cut 2's rows (`001 - 2 Chapter Two…`, `002 - 3 Closing
   Chapter.pdf`), alerts gone. The reviewer's `mfail.py` now times out on purpose: it waits for a third row that
   the new cut does not have.
3. **One split per click (F3).** `Download.svelte:37` `requestedOn` (the status at the click) keeps Split disabled
   (`:44`) from the click until the first later status that reports a cut (`kind: cut`, state ≠ `review` — the
   `$effect` after `disabled`), so the gap between the 202 and the first poll cannot take a second click. A 409
   `busy` answer to our own POST (`:74`) is followed like a queued cut (no alert; `oncut`); any other refusal frees
   the button; every split error clears once a later status arrives (same effect). Tests: `Download.test.ts`
   (after the 202, before any poll: disabled, two more clicks → 1 POST, no alert; queued → disabled; done → enabled
   → a second cut posts; `busy` → no alert + `oncut`; a 500 frees the button and its message goes on the next
   status), `JobPage.test.ts` unchanged AC-1 test still asserts `cut` called once.
   Live (`dbl2.py`, 160 ms latency, second click at 50 ms and at 250 ms — inside the old 167→344 ms gap): the
   button reads `disabled` from 1 ms to 2015 ms (done), the second click lands on a disabled button, exactly one
   `POST /cut`, `alerts after the cut finished: []`.

Counts after the fix: `bun run test` **238 pass** (17 files; +10), `bun run check` 0 errors 0 warnings (330 files),
`bun run build` 102.16 kB JS (36.57 kB gzip); `uv run pytest -q` **345 pass** (+1), `uv run ruff check` clean.
Maciocia loop re-run (`flow.py`, API :8031 + worker + Vite :5231): 23 rows in 13.8 s (7 progress lines, 31
badges), `004 - Copyright page.pdf` 128,178 B and `maciocia-sections.zip` 53,874,969 B through the proxy, an edit →
"Ready for review" / "Files from the last cut" with 23 rows and Split enabled, Delete now (confirm) → "Your files
were deleted" (one "Split another PDF" link, no status card), reload → "This job was deleted". `document.title`
stays "PDF Splitter".

Decisions: `seconds_left` is derived at response time (not stored); the grace after zero (1.5 s) also paces re-asks
if the API keeps answering 200 with 0 left (the server compares whole-second strings, so the 410 follows within a
second). The old rows are cleared on the `done` that ends a cut, not on the click: a failed cut keeps the previous
ZIP downloadable (Architecture § Job states).

## Gate r2 fixes (2026-09-25)

Review: `docs/findings/STORY-011-review.md` round 2 — 2 confirmed (incomplete F1, F3 regression). One commit on the
feature/mvp tip, `fix: STORY-011 - gate r2: re-check the server after wake, never strand the Split button`. Its 8 new
tests were run against `9eeb9a2` in a scratch worktree: 7 fail there (the five `after a suspend` cases and both
"first status is already review" cases); the eighth (`Download.test.ts` "a status that answers while the POST is
out…") passes on both and guards the new anchor against regressing, it is not a repro.

1. **Wake re-check (F1).** `JobPage.svelte:83` `recheck()` bumps `resume` (one more `JobStatus` poll) while the
   page is `settled` (`:75` — a terminal status and not gone; while queued/running the loop polls anyway, so a
   wake-up never doubles it). Triggers: `<svelte:document onvisibilitychange>` when `visible`, `<svelte:window
   onpageshow ononline>` (`:111-112`), and an interval (`:86-108`, `tickMs` = `EXPIRY_TICK_MS` 60 s) that re-checks
   every `recheckMs` (`WAKE_RECHECK_MS` 5 min, `config.ts:38`) AND at once when the wall clock has moved more than
   a minute further than the monotonic one since the last tick (`:99` — a suspend the page slept through; on a
   headless or lidless machine no event fires, and the reviewer's `suspend.py` fires none either). The wall clock
   still never sets the countdown: it only prompts asking the server, and each answer re-anchors the countdown
   (`onstatus` → `receivedAt`; `Expiry.svelte:44` re-reads its clock at every status, `:52` subtracts whole seconds
   like the server counts so a fresh 3600 reads "1 h", not "59 min"). Only the poll's 404/410 shows the deleted
   screen (unchanged). One poll per event: the three events fired back to back in one task are batched into one
   effect run by Svelte, fewer never more.
   Tests: `JobPage.test.ts` `describe('after a suspend (gate r2 F1)')` (:294) — `suspended()` moves `Date` 24 h and
   the API's next `seconds_left`, leaves `performance.now()` and the timers alone: visibilitychange → exactly one
   poll → "Files deleted in 30 min.", page stays; pageshow → 410 → deleted screen after exactly one poll; one poll
   per event, none while `hidden`, none on top of a running cut's loop (a hanging `load` counts calls); a Date jump
   with no event → one re-check within `tickMs`, then nothing; `recheckMs: 40` → the settled page polls by itself
   and shows the server's new count.
   Live (reviewer's `rv011-r2/suspend.py`, API :8041 + worker + Vite :5241, Playwright clock +24.5 h, deadline
   moved into the past in `jobs.db`, no event fired): 65 s after "waking": deleted screen `expired`, 1 poll, 0
   rows. Same script, 22 h jump with 1800 s left server-side after a cut: "Files deleted in 29 min.", 3 rows + ZIP
   link still listed, the first section link answers 200, 1 poll (`shots/s3-suspend.png`, `s4-suspend.png`).
2. **Split never stranded (F3).** `Download.svelte:78` anchors `requestedOn` AFTER the API accepted the cut (202 or
   our own `busy`), not at the click — a status that answered during the POST still describes the job before it —
   and `:54` releases it on ANY later status whose `kind` is `cut` (`review` included: the cut finished and an edit
   landed before the poll's first answer) or that is `failed`. The page restarts the poll on the 202 (`oncut` →
   `resume++`, which aborts an in-flight poll), so every status after the anchor is post-cut. `JobPage.svelte:64`
   treats that first `review`/`done` `cut` status as the cut finishing (`cuts++`), so `Review.loadResults()` fetches
   the manifest the new ZIP serves. Double-click stays one POST: `posting` covers the POST, `requestedOn` the gap to
   the first poll.
   Tests: `Download.test.ts:123` (202 → clicks → 1 POST → `review/cut` frees the button, no alert), `:134` (a
   `review/cut` rerender while the POST is out does not free it; queued keeps it; done frees it), `JobPage.test.ts:268`
   (cut 1, edit → review, then `api.straightTo = 'review'`: two clicks → 2 POSTs total, status `review`, "Files from
   the last cut" lists `003 - 3 The End.pdf` and not cut 1's file, Split enabled, no alert).
   Live (reviewer's `stuck.py`: status route aborted from the click through cut 2 and an edit saved meanwhile): 8 s
   after the network is back — "Ready for review", `Split disabled=False`, rows `001…`, `002 - Renamed Two.pdf`,
   `003…` = the server's manifest, alerts `[]`; still enabled 28 s later. `slowedit.py` (300 ms latency on every
   request, rename 1.5 s after the click): 20 s later "Ready for review", Split enabled, "Saved", server review/cut.

Counts after the fix: `bun run test` **246 pass** (17 files; +8), `bun run check` 0 errors 0 warnings (330 files),
`bun run build` 102.81 kB JS (36.84 kB gzip). No Python change (`uv run pytest` untouched at 345).

Decisions: the drift trigger is an addition to the kickoff's four (it is what makes the reviewer's event-less repro
pass; threshold = the countdown's minute resolution, so `vi.waitFor` nudging a faked `Date` by 50 ms never trips
it); `settled` includes `failed` (its countdown is just as stale after a sleep); the periodic re-check runs only
while settled, so a page mid-cut is not polled twice.
