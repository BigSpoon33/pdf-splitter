# pdf-splitter

A public website on top of [pdf-splitter-engine](https://github.com/BigSpoon33/pdf-splitter-engine) (Python package `monograph-splitter`): drag in a large text-layer PDF, choose how sections are found (PDF outline, big headings, or a pasted list), tune the layout (columns, bands), preview and drag the cuts, and download one PDF per chapter/entry.

Planning: [`docs/PRD.md`](docs/PRD.md) · [`docs/Architecture.md`](docs/Architecture.md) · [`docs/stories/`](docs/stories/)

## Development

Python 3.12 and [uv](https://docs.astral.sh/uv/). The engine is installed from its public GitHub tag (`pyproject.toml`).

```bash
uv sync                      # create .venv, install deps + dev tools
uv run pytest                # tests
uv run ruff check            # lint
uv run pdf-splitter api      # API on http://127.0.0.1:8000 (--host / --port to change)
curl -s localhost:8000/api/health
curl -s -F file=@book.pdf localhost:8000/api/jobs   # 201 {id, state} or 4xx {code, message}
uv run pdf-splitter worker   # run queued jobs (same PDFSPLIT_JOBS_DIR as the api)
```

The worker claims queued jobs, at most `PDFSPLIT_WORKERS` at a time, and runs each as
`python -m pdf_splitter.task <kind> -- <id>` through `pdf_splitter.worker.sandbox` (RLIMIT_AS 2 GB, RLIMIT_CPU
timeout + 10 s, RLIMIT_FSIZE 1 GB) under the kind's wall-clock timeout. An analyze job writes
`<id>/analysis.json`, `<id>/plan.json` and the engine index `<id>/work/.book-index.json`, then moves the row to
`review`. A cut job reads `<id>/plan.json`, writes one PDF per section into `<id>/work/` and then
`<id>/result.zip` (`NNN - <section name>.pdf` × n + `manifest.json`), replaced whole, and moves the row to `done`.
A failure leaves `failed` with `error_code` `timeout`, `resources` or `internal`. SIGTERM stops claiming and
lets running jobs finish.

Request logs come from the app (`pdf_splitter.access`), not uvicorn, so job ids appear only as `log_id` hashes.
Every response carries an `X-Request-ID` (an inbound one is kept when it matches `[A-Za-z0-9-]{8,64}`); the same
id ends the access-log line, and an unexpected error is a 500 `{code: "internal", message, request_id}` whose
redacted traceback is logged under that id.

### API

Job ids are the only credential (capability URLs); errors are `{code, message}` (`src/pdf_splitter/errors.py`
has every code), 422s add `errors: [{loc, msg, type}]`.

```
POST   /api/jobs                             multipart file → 201 {id, state}
GET    /api/jobs/{id}                        {id, state, kind, progress, total, queue_position, message,
                                              error_code (failed only), expires_at, seconds_left, filename, pages}
                                              (seconds_left: until expires_at by the server's clock — the SPA counts
                                              down from it; only the 410 below declares a job gone)
                                              404 not_found · 410 expired (deleted or past its TTL)
GET    /api/jobs/{id}/analysis               the Analysis (409 not_ready before review)
GET    /api/jobs/{id}/plan                   the saved Plan
PUT    /api/jobs/{id}/plan                   Plan → 200 normalized Plan · 422 invalid · 409 busy while a job runs;
                                              from done the job returns to review
POST   /api/jobs/{id}/cut                    202 {id, state: "queued"} · 409 busy/not_ready · 422 empty plan
GET    /api/jobs/{id}/sheets/{n}.png?dpi=72  PNG of sheet n (dpi 48|72|110), cached per (sheet, dpi, settings)
POST   /api/jobs/{id}/sections/{i}/plan      {settings?, override?, sections?} → the Section plan (rects on 1-based sheets)
GET    /api/jobs/{id}/result.zip             attachment (409 not_ready before the first cut)
GET    /api/jobs/{id}/sections/{i}.pdf       attachment, straight out of result.zip
DELETE /api/jobs/{id}                        204 (the row is marked deleted, then the directory removed)
GET    /api/health                           {ok, queue, disk_free_gb, engine_version}
```

Previews run the engine read-only in a `python -m pdf_splitter.preview` subprocess under the worker's rlimits
with a 20 s timeout. A Plan whose `settings` differ from the index's re-indexes the book inside that window
(≈ 12–14 s for 1300 pages), so the first preview after a layout change is slow and the ones after it are not.

### Configuration

All settings are environment variables with the `PDFSPLIT_` prefix (`src/pdf_splitter/config.py`).

| Variable | Default | Meaning |
|----------|---------|---------|
| `PDFSPLIT_JOBS_DIR` | `./jobs` | uploads, outputs and `jobs.db` (production: `/jobs`) |
| `PDFSPLIT_MAX_BYTES` | `209715200` | largest upload, in bytes (200 MiB = 200 × 1024 × 1024) |
| `PDFSPLIT_MAX_PAGES` | `2000` | largest page count |
| `PDFSPLIT_TTL_HOURS` | `24` | hours a job and its files are kept |
| `PDFSPLIT_WORKERS` | `2` | concurrent analyze/cut jobs |
| `PDFSPLIT_RATE_PER_HOUR` | `6` | uploads per IP per hour |
| `PDFSPLIT_ANALYZE_TIMEOUT` | `300` | analyze job wall-clock limit, seconds |
| `PDFSPLIT_CUT_TIMEOUT` | `600` | cut job wall-clock limit, seconds |
| `PDFSPLIT_PUBLIC_URL` | `http://localhost:8000` | the site's public base URL |

## Web

The SPA lives in `web/` (Svelte 5 + Vite + TypeScript, [Bun](https://bun.sh) only). In development Vite serves
it and proxies `/api` to the API on `localhost:$API_PORT` (default 8000), so the browser stays same-origin.

```bash
cd web
bun install
API_PORT=8000 bun run dev    # http://localhost:5173 (run the api + worker above on API_PORT)
bun run check                # svelte-check (TypeScript strict)
bun run test                 # vitest run (jsdom; no API or network needed)
bun run build                # static files in web/dist/
```

`API_PORT` can also go in `web/.env.local`. `VITE_MAX_BYTES` (build time) changes the client-side size
precheck; it should match `PDFSPLIT_MAX_BYTES`. Routes are `/` (upload), `/j/<id>` (a job), `/privacy` and `/terms`,
so whatever serves `web/dist/` in production must answer `index.html` for all of them.

Once a job reaches `review`, `/j/<id>` shows the plan editor: the source picker (outline level, detected headings by
size threshold and level, or a pasted `Name, page` list), the editable section list (rename, page, add, delete,
merge-with-next; after a cut, the manifest's flags as badges) and the layout panel (columns, gutter, header/footer
bands, heading size, heading wrap gap). Every edit is `PUT` to `/api/jobs/{id}/plan` 600 ms after the last
keystroke; a 422 shows next to the field it names.

**Split** posts `/api/jobs/{id}/cut` (a pending edit is saved first); the status poll resumes and follows the cut
section by section, then the results list shows one download link per section (with its flags) and "Download all
(ZIP)" — plain `<a download>` links to the API, never fetched into memory. An edit after a cut puts the job back in
`review` while the last cut's files stay downloadable until the next cut; a new cut empties that list first, and a
refresh that fails shows an error with Retry (one automatic retry) rather than the previous cut's files. "Files
deleted in N h" (counting down from the status's `seconds_left`, never from the visitor's clock) and **Delete now**
(confirm → `DELETE /api/jobs/{id}`) are always on the job page; a deleted, expired or unknown job (the API's 404/410 —
when the countdown runs out the page asks once more) turns the whole page into the deleted screen with a "Split
another PDF" link.
