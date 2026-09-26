# Findings — STORY-011
**Date:** 2026-09-25
**Status:** done

Commit: `68769d3 feat: STORY-011 - split, download, delete-now and expiry UX` (feature/mvp, pushed to `origin` and
`gitea`). Web + README § Web only; nothing in `src/pdf_splitter/`, `tests/` or the engine changed. Lines below are
as of that commit.

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
