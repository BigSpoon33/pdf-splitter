# PRD: pdf-splitter

> **Status:** Draft
> **Date:** 2026-09-25
> **Author:** Analyst phase — DevCycle

---

## Problem Statement

A large reference PDF, such as a 1,600-page textbook, a materia medica or a formulary, is too big
to study from, share a section of, or feed to a tool one chapter at a time. Splitting it by
hand means finding every chapter's first page, deciding where it ends, and dealing with two-column
layouts where one chapter ends halfway down a column and the next begins beneath it. Generic
"split by page range" tools cut whole pages, so every mid-page boundary leaks the neighbouring
section into the excerpt. `monograph-splitter` already solves the hard part (column- and
band-aware cuts with real redaction, and verification of what leaked). Today it is usable only by
someone who can write a TOML layout profile and a JSON entry list and run a Python CLI.

## Users

| User | Context | Primary Need |
|------|---------|--------------|
| Student / practitioner (anonymous, public) | Has a big textbook PDF and wants one PDF per chapter or per entry | Drop the file in, get clean per-section PDFs back without installing anything |
| Power user (e.g. Shuma) | A two-column reference book with entries that start mid-page | Tune detection and cut lines visually instead of editing TOML/JSON and reading y-values off PNGs |
| Operator (Shuma) | Runs the site on one cloud VM | Bounded cost, no storage creep, no abuse vector, redeploy in one command |

## Scope

### In Scope

- A public web page. Drag in a PDF (text-layer PDFs only), then watch upload and analysis progress.
- **Three ways to find sections**, switchable in the UI:
  1. The **PDF outline/bookmarks**, with the user picking the outline level (chapters vs sections).
  2. **Big-heading detection** by font size, tunable (minimum heading size relative to body text, header/footer bands, max heading length).
  3. **Paste or edit an entry list** (`Name, page` per line), which also covers editing the output of 1 or 2.
- An editable section list: rename, delete, add, change start page, merge with the next section.
- **Layout settings**: single vs two-column, the column split position (a draggable gutter on the preview), and the header/footer bands to keep or strip.
- **A page preview** showing each section's first and last page, with the removed regions hatched. The user can drag a section's start/end cut line.
- Cutting runs server-side with the existing engine, then gives a download as a ZIP (one PDF per section plus `manifest.json`) or as individual PDFs.
- Anonymous, free and capped: no accounts, an unguessable job link as the only handle, upload size and page caps, a per-IP rate limit, and **every file deleted 24 h after upload** (or on "Delete now").
- Deployment to one Hetzner/DigitalOcean VM with Docker Compose and Caddy (automatic TLS) on a public domain.
- A privacy/terms page: files are private to the link holder, deleted after 24 h, and a takedown contact is given.

### Out of Scope

- **OCR / scanned PDFs without a text layer.** v1 detects them and refuses them with a clear message. The pipeline needs ocrmypdf + tesseract and minutes of CPU per book, so it is deferred to v2.
- **Accounts, saved settings, payment.** The architecture leaves room for them (job ownership column), but v1 builds none of it.
- **Exposing the five bundled book profiles** (Chen & Chen, Maciocia, Bensky) and labels-mode detection in the UI, because those are tuned to specific copyrighted books. The engine keeps them for the Inkwell adapters.
- **Hosting or sharing split output publicly.** There is no gallery, no public links and no search.
- Horizontal scaling, multi-region, or a CDN. One VM is the target.
- Editing PDF content (text, annotations) beyond redacting what lies outside a section.
- Inkwell integration. Inkwell keeps using the engine directly.

## Acceptance Criteria

| # | Criterion | How to Verify |
|---|-----------|---------------|
| AC-1 | A text-layer PDF of ≤ 200 MB / ≤ 2,000 pages uploads by drag-and-drop and reaches "ready to review" | Upload the Maciocia Foundations PDF (~700 pp) on the deployed site; the status reaches `review` and a section list appears |
| AC-2 | A PDF with an outline yields one section per outline item at the chosen level | Upload a PDF with bookmarks; switch to "Outline, level 1"; the section count equals `len([t for t in doc.get_toc() if t[0]==1])` |
| AC-3 | Heading detection finds chapter headings on a PDF without an outline, and the size threshold changes the result | Upload a PDF with no outline; the default threshold lists its chapters; raising the threshold shrinks the list |
| AC-4 | A pasted `Name, page` list replaces the section list | Paste 3 lines; the list shows exactly those 3 sections |
| AC-5 | In a two-column PDF, a section that starts mid-column is cut at the heading and does not leak the previous section | On a Maciocia pattern that starts mid-page, the downloaded PDF's first page shows the previous text redacted; the engine's `--verify` equivalent reports 0 leaks for it |
| AC-6 | Dragging a cut line in the preview changes the output | Move a section's start cut; re-cut; the downloaded PDF's first page starts at the new y (±2 pt) |
| AC-7 | The ZIP download holds one PDF per section plus `manifest.json` | `unzip -l` lists N PDFs + manifest.json for N sections |
| AC-8 | A scanned PDF with no text layer is refused with an explanatory message, not a crash | Upload an image-only PDF; the UI shows "no text layer — OCR isn't supported yet" |
| AC-9 | Oversized, encrypted, or non-PDF uploads are refused before any processing | Upload a 300 MB file, a password-protected PDF and a renamed `.png`; each gets a specific 4xx message |
| AC-10 | Every upload and its outputs are deleted ≤ 24 h after upload; "Delete now" deletes immediately | Set the TTL to 1 min in staging; after the janitor runs, the job dir is gone and the job URL returns 410 |
| AC-11 | One client cannot starve the service | The 7th upload from one IP within an hour gets 429; at most `WORKERS` jobs process concurrently and the others show "queued, position N" |
| AC-12 | A malicious/malformed PDF cannot hang or take down the service | A PDF that makes MuPDF loop is killed at the job timeout, marked `failed`, and the next job runs |
| AC-13 | The site is served on a public HTTPS domain from one VM, deployed by one command | `curl -I https://<domain>/` gives 200 with a valid cert; `./deploy.sh` redeploys |

## Constraints

| Constraint | Description | Source |
|------------|-------------|--------|
| Engine reuse | Cutting, redaction and verify are `monograph-splitter`'s. The web service must not fork or re-implement them. Engine changes land in that repo under a version tag, and web pins the tag | Existing engine + Inkwell adapters depend on it |
| Engine regression gate | Engine changes must keep `monograph-splitter-diff` at 0 on Inkwell's known-good books | Engine README "Regression gate" |
| Stack | Python (FastAPI/Pydantic, `uv`) backend. Frontend in TypeScript built with Bun. SQLite for job state (one VM) | Chrono stack defaults |
| Hosting | One Hetzner/DO VM (2–4 vCPU, 4–8 GB RAM), Docker Compose, Caddy. `*.gumshu.duckdns.org` is LAN-only, so it needs a real public domain | Shuma, 2026-09-25 |
| Untrusted input | Every PDF is hostile until proven otherwise. MuPDF parsing runs in a resource-limited, network-less worker | Public service |
| Cost | Target ≤ $10/mo. Disk is bounded by caps × concurrency × 24 h retention | Operator |

## Assumptions

| # | Assumption | Risk if Wrong |
|---|------------|---------------|
| A-1 | Headings mode (the Maciocia path: `{name, page, heading}` → located heading → column-aware cut) generalises to arbitrary text-layer books given detected headings | The cut quality on unfamiliar layouts is poor. Mitigation: the preview + manual cut drag is the escape hatch, and the heading detector gets its own test corpus |
| A-2 | Most "large PDFs" users bring have either an outline or visibly larger heading type | If neither, users must paste a list (still supported, just slower) |
| A-3 | Indexing ≤ 2,000 pages of a digital-text PDF takes ≤ 2 min on 2 vCPU. The 5-min figure is the OCR'd Chen & Chen scan | If slower: lower the page cap or add workers |
| A-4 | Users upload books they have the right to process. Output is private and deleted in 24 h, so the service is a tool, not a distributor | A takedown/abuse complaint. Mitigation: terms, takedown contact, no public links, short retention |
| A-5 | Page-number semantics: the web mode uses **sheet numbers** (PDF page 1 = first page; engine `sheet_offset = 0`, because the engine maps page N to sheet index `N + sheet_offset − 1`). Printed page labels are shown alongside when the PDF has them | Users think in printed page numbers. The UI shows both |

## Open Questions

| # | Question | Owner | Deadline |
|---|----------|-------|----------|
| Q-1 | Product name + domain (e.g. `splitmybook.app`?) | Shuma | Before STORY-014 |
| Q-2 | Hetzner or DigitalOcean account, and which region | Shuma | Before STORY-014 |
| Q-3 | Final caps: 200 MB / 2,000 pages / 6 uploads per IP per hour / 2 workers? | Shuma (defaults stand unless changed) | Before STORY-012 |
| Q-4 | Takedown/contact email shown on the terms page | Shuma | Before STORY-014 |

## MVP Definition

**MVP includes:** upload → analyze (outline + heading candidates) → review (switch source, edit list, layout settings, cut preview + drag) → cut → ZIP/PDF download → 24 h deletion. Anonymous + capped, deployed on one public VM.

**V2 and beyond:** OCR for scans (ocrmypdf), accounts + saved presets, paid tier (bigger caps), labels-mode "entry header block" detection for reference books, per-section Markdown/text export, an API for programmatic use, and multi-file batches.

## Notes

- The engine was spun out of Inkwell (STORY-207). Its review editor (`monograph-splitter-review`, v0.3.0) is a single-user, local, config-file-driven FastAPI + vanilla JS page. The web service borrows its plan/preview/override *interaction model*, but not its code as-is: that editor trusts its config, serves from arbitrary local paths, and runs shell hooks.
- The engine repo's `main` currently has 1 unpushed commit (v0.3.1) and an uncommitted README change from another session. Engine stories must start from a pushed baseline (see STORY-001).
