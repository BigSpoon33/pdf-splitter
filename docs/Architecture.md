# Architecture: pdf-splitter

> **Status:** Draft
> **Date:** 2026-09-25
> **PRD:** `docs/PRD.md`
> **Author:** Architect phase — DevCycle

---

## System Overview

A browser uploads a PDF to a FastAPI service. The service validates it, stores it under an
unguessable job id and queues an **analyze** job. A separate worker container runs the job in a
resource-limited subprocess: it opens the PDF with `monograph-splitter`, indexes every page's
lines, and extracts two candidate section lists, from the PDF outline and from big-heading
detection. The user reviews these in a Svelte single-page app, where they pick a source, edit the
list, set the layout (columns, bands), preview cuts on rendered pages and drag cut lines. The
user's choices form a **Plan** (sections + settings + overrides), which is saved as JSON. A **cut**
job then feeds the Plan to the engine's headings mode, which locates each heading on its sheet,
cuts column- and band-aware, redacts the neighbours' share of shared pages and verifies. The job
then zips the excerpts with a manifest for download. A janitor deletes every job 24 h after upload.

---

## Component Map

```
monograph-splitter (engine, github.com/BigSpoon33/pdf-splitter-engine, v0.4.1)          ← library; the ONLY code that cuts PDFs
├── profile.profile_from_dict / WEB_KEYS          ← build a Profile from JSON settings (whitelisted keys)
├── detect.outline_entries / heading_candidates   ← NEW: candidate section lists from a PDF
├── session.Book.open(entries=EntryList|list)     ← accepts in-memory entries, no files needed
└── session.Book.cut_all(progress=…)              ← cut every entry, report progress

pdf-splitter (this repo, github.com/BigSpoon33/pdf-splitter)
├── api/            FastAPI app (uvicorn)         ← HTTP, validation, rate limits, job state, file serving
│   ├── upload      streaming upload + preflight
│   ├── jobs        status, plan read/write, trigger cut, delete
│   ├── preview     sheet PNGs + per-section cut plans (read-only engine calls, short timeout)
│   └── download    zip / single PDF
├── worker/         queue consumer (separate container)  ← analyze + cut jobs in a sandboxed subprocess
│   ├── runner      claim job → spawn `python -m pdf_splitter.worker.task` with rlimits + timeout
│   └── task        the engine calls (analyze.py, cut.py)
├── janitor         loop in the worker container   ← delete expired jobs, reap orphans
├── store           SQLite (jobs table) + job directory layout
└── web/            Svelte + Vite + TS SPA (built by Bun, served as static files by Caddy)
    ├── DropZone · JobStatus · SourcePicker · SectionList · LayoutPanel · PagePreview · Download
    └── api.ts (typed client)

deploy/
├── Dockerfile (python: api + worker image), web build stage
├── compose.yaml (caddy, api, worker; volume `jobs`)
└── Caddyfile (TLS, static SPA, /api → api:8000, body size cap)
```

### Component Details

#### Engine additions (monograph-splitter 0.4.0)

- **Responsibility:** Everything that understands PDFs. The web repo never imports `fitz` for
  analysis beyond the preflight (page count, encryption, text-layer probe) and PNG rendering.
- **Inputs:** a PDF path; a settings dict; an entry list (in memory).
- **Outputs:** candidate lists; the Book session (plan/cut/manifest) exactly as the CLI makes it.
- **New surface:**
  - `profile_from_dict(d: dict, base: Profile = WEB_BASE) -> Profile`. It accepts only `WEB_KEYS`
    (`column_split`, `header_band`, `footer_band`, `redact_top`, `heading_min_size`,
    `heading_match`, `heading_wrap_gap`, `max_span`, `single_column`), and an unknown key is a
    `ProfileError`. `WEB_BASE` = `anchor_source="headings"`, `sheet_offset=0` (page 1 = sheet index 0),
    `script_regex=""`, `break_patterns=()`, `max_span=200`. `sha256` = the hash of the canonical
    JSON, so the index cache stays keyed correctly.
  - `single_column: bool` → sets `column_split` so that every line is "left" (e.g. 0.999) and
    `full_width_ratio` low, so all cuts are full-width. Implemented in `profile_from_dict`, with no
    engine branching.
  - `detect.outline_entries(doc, level: int) -> list[dict]` →
    `[{name, page, heading, level}]` from `doc.get_toc(simple=False)`. `page` = the destination
    sheet (1-based). `heading` = the outline title, and the destination y (when the outline has
    one) is carried as `y` for the locator to prefer.
  - `detect.heading_candidates(doc, *, min_ratio=1.3, max_len=90, header_band, footer_band) -> dict`:
    body size = the char-weighted mode of glyph sizes. A candidate = a line with
    `size ≥ body × min_ratio`, `len ≤ max_len`, outside the bands, merged with wrapped lines
    (`heading_wrap_gap`). Candidates are grouped into **levels** by size cluster (largest = level 1).
    Returns `{body_size, levels: [{size, count}], candidates: [{name, page, heading, size, level, y, col}]}`.
    It is pure: one pass over `page.get_text("dict")`, reusing `index.page_lines`.
  - `Book.open(..., entries: EntryList | list[dict] | Path)`. A list goes through the same
    validation as `load_entries_json` (factored into `entries_from_rows`).
  - `Book.cut_all(progress=None, verify=True, preview=False, only=None, *, limit=None, redact=True) -> dict`
    returning `{written: [manifest rows], flags: {flag: n}, notes: {note: n}, leaks: {name: [str]}, missing: [str], unknown: [str]}`;
    `progress(done, total, name)` after each entry. One `Book` per cut job (`missing` accumulates on a Book).
    Entry names that contain `/`, `\`, NUL or are `.`/`..`/empty are refused (`ValueError`) before any write
    (`session.safe_filename`). `heading_candidates(profile=…)` takes bands/column split/wrap gap from the same
    Profile the cut uses — the worker passes ONE `profile_from_dict(settings)` to both. Engine version is
    `monograph_splitter.__version__` (the int `ENGINE_VERSION` is only the index-cache key).
    `cli.main`'s loop moves here, and the CLI calls it (diff gate 0).
- **Failure mode:** loud. A bad settings key raises `ProfileError`. A heading not found → the
  entry gets a whole-page start + a `heading-not-found` flag (the existing behaviour), which is
  shown in the UI.

#### api

- **Responsibility:** the HTTP surface, input validation, rate limiting, job state transitions,
  and serving outputs. It never runs a full analysis or cut in-process.
- **Inputs:** multipart uploads (streamed to disk), JSON Plans, job ids.
- **Outputs:** JSON, PNG, PDF, ZIP.
- **Preview calls** (`/sheets/{n}.png`, `/sections/{i}/plan`) run the engine *read-only* in a
  subprocess from a small pool (`preview_timeout = 20 s`, same rlimits as the worker), using the
  index the analyze job cached. A PNG render is cached on disk per (sheet, dpi, settings-hash).
- **Failure mode:** each rejection is a specific 4xx with a `code` (`too_large`, `too_many_pages`,
  `encrypted`, `not_pdf`, `no_text_layer`, `rate_limited`, `expired`). Unexpected errors → 500
  with a request id. Nothing leaks internal paths.

#### worker

- **Responsibility:** run analyze and cut jobs, one subprocess each, at most `WORKERS` at a time.
- **Inputs:** queued job rows.
- **Outputs:** `analysis.json`, `out/*.pdf`, `manifest.json`, `result.zip`, progress in the job row.
- **Sandbox:** the subprocess gets `RLIMIT_AS` (2 GB), `RLIMIT_CPU` (job timeout + 10 s),
  `RLIMIT_FSIZE` (1 GB) and a wall-clock timeout (analyze 5 min, cut 10 min). The worker container
  has **no network** (`network_mode: none`), a read-only root fs, and runs as non-root. The only
  writable mount is `/jobs`.
- **Failure mode:** a timeout, rlimit kill or exception → the job becomes `failed` with a
  user-facing reason and the stderr tail is logged. The worker loop continues. A worker restart
  re-queues jobs that were `running` for longer than their timeout.

#### janitor

- **Responsibility:** every 5 min, delete job dirs + rows where `expires_at < now`, delete
  orphan dirs with no row, and cap total disk (refuse new uploads with 503 when `/jobs` has
  < 2 GB free).
- **Failure mode:** logs and continues. Deletion is idempotent.

#### store

- SQLite in WAL mode at `/jobs/jobs.db`, shared by api and worker via the volume. Jobs are
  claimed with `UPDATE … WHERE id = (SELECT … WHERE state='queued' ORDER BY created_at LIMIT 1)
  RETURNING` inside `BEGIN IMMEDIATE`.

#### web (SPA)

- **Responsibility:** the whole UX. It is stateless apart from the job id in the URL
  (`/j/<id>`), so a reload resumes.
- **Components:** `DropZone` (drag, file-type/size precheck, upload progress via XHR) ·
  `JobStatus` (polls `GET /api/jobs/{id}` every 1.5 s while running; shows queue position) ·
  `SourcePicker` (Outline [level] / Headings [threshold, level] / Paste list) · `SectionList`
  (rename, delete, add, page, merge-with-next, flags badges) · `LayoutPanel` (one/two columns,
  gutter %, header/footer bands) · `PagePreview` (sheet PNG + SVG overlay: gutter line, bands,
  hatched removed regions, draggable start/end cut lines) · `Download`.
- **Failure mode:** API errors render the `code`'s message in place. An expired job shows "This
  job was deleted (files are kept 24 h)".

---

## Data Flow

```
Browser ──upload──▶ api ──preflight (fitz: pages, encrypted, text probe)──▶ /jobs/<id>/source.pdf
                     │                                                       jobs row: queued/analyze
                     ▼
                  worker ──subprocess──▶ engine.index_book (cache .book-index.json)
                                         detect.outline_entries(level 1..3)
                                         detect.heading_candidates()
                                    ──▶ /jobs/<id>/analysis.json         row: review
Browser ◀──GET analysis── api
Browser ──PUT plan (sections, settings, overrides)──▶ api ──validate──▶ /jobs/<id>/plan.json
Browser ──GET sheet PNG / section plan──▶ api ──preview subprocess (read-only engine)──▶ PNG/JSON
Browser ──POST cut──▶ api ──▶ row: queued/cut
                  worker ──subprocess──▶ profile_from_dict(settings) → Book.open(entries=sections)
                                         set overrides → Book.cut_all(verify) → out/*.pdf, manifest.json
                                    ──▶ result.zip                         row: done
Browser ──GET result.zip / section PDF──▶ api (FileResponse, attachment)
janitor ──every 5 min──▶ delete expired /jobs/<id> + row
```

### Data Types

| Type | Shape | Where Created | Where Consumed |
|------|-------|---------------|----------------|
| Job row | `{id, state, kind, created_at, expires_at, ip_hash, filename, pages, bytes, progress, total, message, error_code}` | api | api, worker, janitor |
| Analysis | `{pages, pageLabels[], size:{W,H}[], outline:{levels:[n1,n2,n3], items:[{name,page,heading,level,y?}]}, headings:{body_size, levels:[{size,count}], candidates:[{name,page,heading,size,level,y,col}]}, suggested:{source, level}}` | worker/analyze | web |
| Plan | `{source:"outline"\|"headings"\|"manual", settings:{column_split, single_column, header_band, footer_band, heading_min_size}, sections:[{name, page, heading}], overrides:{[sectionIndex]: {startCut, startCol, endCut, endCol}}}` | web | api (validate) → worker/cut, preview |
| Section plan (preview) | the engine's `_plan_view`: `{pages:[a,b], startCut, startCol, endCut, endCol, flags[], rects:[[sheet,[x0,y0,x1,y1]]]}` | preview subprocess | PagePreview |
| Manifest | the engine's `manifest.json` rows + `{file}` | worker/cut | download zip |

`sections[].name` is the DISPLAY name (≤ 120 chars; duplicates allowed in the UI). The engine never sees it:
the worker passes each section to the engine as a unique, filename-safe `NNN-<ascii-slug>` (≤ 80 bytes), keeps
the display name in the Plan, and translates `overrides` (keyed by section INDEX in the Plan) to engine names.
ZIP entries are `NNN - <display name, sanitized>.pdf` (the index prefix keeps book order). Plan validation
rejects `page ∉ [1, pages]` (the engine's `cuts.plan` raises IndexError past the book end).

---

## Interfaces

### API Interface

```
POST   /api/jobs                       multipart file=<pdf>
  201: {id, state:"queued"}            400 not_pdf|encrypted|no_text_layer · 413 too_large|too_many_pages · 429 rate_limited · 503 disk_full
GET    /api/jobs/{id}                  {id, state, kind, progress, total, queue_position, message, error_code, expires_at, filename, pages}
                                       404 unknown · 410 expired
GET    /api/jobs/{id}/analysis         Analysis (409 until state ≥ review)
GET    /api/jobs/{id}/plan             Plan (default = suggested source, no overrides)
PUT    /api/jobs/{id}/plan             Plan → 200 normalized Plan | 422 with field errors
GET    /api/jobs/{id}/sheets/{n}.png?dpi=72   PNG (dpi ∈ {48, 72, 110})
POST   /api/jobs/{id}/sections/{i}/plan       {settings?, override?} → Section plan (not persisted)
POST   /api/jobs/{id}/cut              202 {state:"queued"} (uses the saved Plan) · 409 if running
GET    /api/jobs/{id}/manifest         the last cut's manifest rows as JSON [{index, name, file, pages, flags, notes, leaks, bytes}] (409 before any cut; kept until the next cut replaces it)
GET    /api/jobs/{id}/result.zip       attachment
GET    /api/jobs/{id}/sections/{i}.pdf attachment (after cut)
DELETE /api/jobs/{id}                  204
GET    /api/health                     {ok, queue, disk_free_gb, engine_version}
```

Job ids are 22-char url-safe base64 of 16 random bytes. They are the only credential, so they
never appear in logs (logs carry a short hash).

### Job states

`queued(analyze) → running(analyze) → review ⇄ queued(cut) → running(cut) → done`. Any state →
`failed` (with error_code), and any → `deleted`. From `done`, editing the Plan returns to
`review`. Old outputs stay downloadable until the next cut replaces them.

### File layout

```
/jobs/jobs.db
/jobs/<id>/source.pdf
/jobs/<id>/work/.book-index.json       engine cache (keyed on settings hash)
/jobs/<id>/analysis.json
/jobs/<id>/plan.json
/jobs/<id>/work/<slug>.pdf, manifest.json, overrides.json   (engine out dir)
/jobs/<id>/result.zip
/jobs/<id>/png/<dpi>/<sheet>-<hash>.png
```

---

## Technology Decisions (ADRs)

### ADR-001: Separate repo, engine as a pinned dependency

- **Status:** Accepted (Shuma, 2026-09-25)
- **Context:** The engine is a library that the Inkwell adapters pin by tag.
- **Decision:** `pdf-splitter` depends on `monograph-splitter @ git+https://github.com/BigSpoon33/pdf-splitter-engine@v0.4.1`. Engine changes go to the engine repo with tests + diff gate, then get a tag bump here.
- **Consequences:** Two-repo stories, but the engine stays clean, and the web service can't regress Inkwell's books.
- **Hosting (2026-09-25):** both repos are public on GitHub (`BigSpoon33/pdf-splitter`, `BigSpoon33/pdf-splitter-engine`) as `origin`; Gitea (`gitea` remote) is a LAN mirror. Inkwell's adapters keep pinning the Gitea URL until repointed.

### ADR-002: Headings mode is the web engine path; labels mode is not exposed

- **Status:** Accepted
- **Context:** Labels mode needs per-book regexes for header label blocks, which is useless for arbitrary books. Headings mode needs only `{name, page, heading}` + a few layout numbers, and it already does column/band-aware cuts (Maciocia: 211 excerpts).
- **Decision:** Every web cut is headings mode with `WEB_BASE` + whitelisted settings. Outline and heading detection produce headings-mode rows.
- **Consequences:** Detection is the new risk (A-1). The preview + drag is the fallback.

### ADR-003: Sheet numbers, not printed pages

- **Status:** Accepted
- **Decision:** Pages are 1-based sheet numbers everywhere in web mode — engine `sheet_offset = 0` (the engine maps page N to 0-based sheet index `N + sheet_offset − 1`; corrected in STORY-001 from a spec error of 1). The UI shows the PDF page label (`page.get_label()`) as a secondary hint.
- **Rationale:** An unknown book has no calibrated offset, and outline destinations are sheets.

### ADR-004: Job queue on SQLite, not Redis

- **Status:** Accepted
- **Rationale:** One VM, ≤ a handful of concurrent jobs. SQLite WAL + `BEGIN IMMEDIATE` claim is enough and needs one less service. Revisit if there's ever more than one worker host.

### ADR-005: Sandboxed subprocess per job in a network-less worker container

- **Status:** Accepted
- **Context:** MuPDF parses hostile input, and has had CVEs. Pathological PDFs can loop or balloon memory.
- **Decision:** A fresh subprocess per job with rlimits + wall timeout. The worker container has `network_mode: none`, a read-only root, runs non-root and drops all caps. The API container's preflight also opens the PDF, but only reads the trailer/page count/first-pages text under a 10 s timeout in a subprocess.
- **Alternatives:** gVisor/Firecracker (overkill for v1; could be added later as a runtime flag).

### ADR-006: Svelte + Vite + TypeScript SPA, built with Bun, served by Caddy

- **Status:** Accepted
- **Rationale:** An interactive canvas-like preview (drag cut lines over page images) outgrows the engine editor's vanilla JS, and Svelte is small and fast. Bun per stack defaults. A static build means no Node server in production.
- **Alternatives:** HTMX (poor fit for drag interactions), React (heavier; no advantage here).

### ADR-007: Anonymous jobs, 24 h retention, capability URLs

- **Status:** Accepted (Shuma, 2026-09-25)
- **Decision:** No accounts. The job id is the capability. `jobs.owner` is a nullable column reserved for v2 accounts. The IP is stored only as a salted hash (for rate limiting), and the salt rotates daily.

### ADR-008: Caddy for TLS + static + proxy on one Hetzner/DO VM

- **Status:** Accepted (Shuma, 2026-09-25)
- **Decision:** `compose.yaml` = caddy + api + worker. Caddy enforces `request_body max_size 210MB` and gets certificates automatically. `deploy.sh` = rsync/git pull + `docker compose up -d --build` over ssh.

---

## Storage Schema

```sql
CREATE TABLE jobs (
  id          TEXT PRIMARY KEY,           -- capability token
  state       TEXT NOT NULL,              -- queued|running|review|done|failed|deleted
  kind        TEXT NOT NULL,              -- analyze|cut (the current/last job kind)
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL,              -- created_at + 24h
  started_at  TEXT,
  ip_hash     TEXT NOT NULL,
  owner       TEXT,                       -- reserved (v2 accounts)
  filename    TEXT NOT NULL,              -- sanitized, display only
  bytes       INTEGER NOT NULL,
  pages       INTEGER NOT NULL,
  progress    INTEGER NOT NULL DEFAULT 0,
  total       INTEGER NOT NULL DEFAULT 0,
  message     TEXT,
  error_code  TEXT
);
CREATE INDEX jobs_queue ON jobs(state, created_at);
CREATE INDEX jobs_expiry ON jobs(expires_at);
CREATE TABLE rate (ip_hash TEXT, at TEXT);   -- sliding window; pruned by the janitor
```

---

## Dependency Map

| Dependency | Type | Version | Why Needed | Fallback |
|------------|------|---------|------------|----------|
| monograph-splitter | git dep | v0.4.1 | the engine | — |
| pymupdf | PyPI | ≥1.24 (pin exact, track CVEs) | PDF parse/render/redact | — |
| fastapi, uvicorn, pydantic-settings, python-multipart | PyPI | current | API | — |
| svelte, vite, typescript | npm via Bun | current | SPA | — |
| Caddy | container | 2.x | TLS + static + proxy | — |
| Hetzner/DO VM | infra | 2–4 vCPU / 4–8 GB | host | — |

---

## Implementation Order

```
STORY-001 engine: profile_from_dict + entries_from_rows + Book.open(entries=list)
STORY-002 engine: detect.outline_entries + heading_candidates      (parallel to 001)
STORY-003 engine: Book.cut_all(progress) + CLI on it + v0.4.0 tag  (after 001, 002)
    ↓
STORY-004 web: scaffold (uv, FastAPI, settings, SQLite store, health, tests)
STORY-005 web: upload + preflight + job create                     (after 004)
STORY-006 web: worker runner + sandbox + analyze task              (after 003, 005)
STORY-007 web: plan/preview/cut/download API + cut task            (after 006)
    ↓
STORY-008 SPA: scaffold + DropZone + JobStatus                     (after 005)
STORY-009 SPA: SourcePicker + SectionList + LayoutPanel            (after 007, 008)
STORY-010 SPA: PagePreview with overlays + draggable cuts          (after 009)
STORY-011 SPA: cut + download + delete + expiry UX                 (after 010)
    ↓
STORY-012 limits: rate limit, disk guard, janitor, queue position  (after 007)
STORY-013 deploy: Dockerfiles + compose + Caddy + local smoke      (after 011, 012)
STORY-014 deploy: public VM + domain + terms page + uptime ntfy    (after 013; needs Q-1, Q-2, Q-4)
```

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Heading detection is poor on unfamiliar layouts | High | Med | Three sources + an editable list + drag cuts. A detector test corpus of synthetic books (tests/fixtures.py style) with 1- and 2-column layouts |
| Outline titles don't match the on-page heading text, so `locate_heading` misses and the section starts whole-page | Med | Med | Carry the outline's destination y when present (a direct anchor). Otherwise fall back to fuzzy locate, then whole-page + a flag shown in the UI |
| A hostile PDF hits a MuPDF bug | Med | High | ADR-005 sandbox, a pinned + monitored pymupdf, no network in the worker |
| Copyright/abuse complaint | Med | Med | Private links, 24 h deletion, terms + takedown contact, no sharing features |
| Disk fills (200 MB × many jobs) | Med | Med | Per-IP rate limit, a disk guard (503), 24 h janitor |
| The engine's `Book` holds an open `fitz` doc for its lifetime, making preview calls expensive | Med | Low | Preview subprocess pool keeps ≤ 1 open Book per job, LRU of 4, reused across calls, killed on timeout |

---

## Deferred Decisions

| Decision | Deferred Until | Reason |
|----------|----------------|--------|
| Product name / domain | STORY-014 | Doesn't affect code (`PUBLIC_URL` env) |
| OCR pipeline (ocrmypdf worker image) | v2 | PRD out-of-scope |
| Accounts / payments | v2 | `owner` column reserved |
| gVisor runtime for the worker | After launch | Measure first |

---

## Notes

- The engine's review editor (`review/server.py`, `app.html`) is prior art for the preview
  overlay math (`rects` from `cut_rects`, hatched regions, y-ruler). STORY-010 should port the
  overlay logic, not the server.
- Reuse `index.page_lines` for heading detection, and don't add a second text-extraction path.
