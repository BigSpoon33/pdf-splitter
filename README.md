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
A failure leaves `failed` with `error_code` `timeout`, `resources`, `too_large_output` (the cut passed its output
budget: `PDFSPLIT_MAX_OUTPUT_BYTES` or 10× the upload, whichever is smaller, never under 256 MB — nothing of that
cut is kept) or `internal`; a failed cut can be edited and cut again, a failed analyze cannot. The worker process
also runs the janitor: at start and every 5 min it marks jobs past their TTL `deleted` and removes their
directories, removes directories with no row (older than 1 h) or whose row is `deleted`, and prunes rate rows
out of the window and `deleted` rows older than 7 days. SIGTERM stops claiming and lets running jobs finish.

Request logs come from the app (`pdf_splitter.access`), not uvicorn, so job ids appear only as `log_id` hashes.
Every response carries an `X-Request-ID` (an inbound one is kept when it matches `[A-Za-z0-9-]{8,64}`); the same
id ends the access-log line, and an unexpected error is a 500 `{code: "internal", message, request_id}` whose
redacted traceback is logged under that id.

### API

Job ids are the only credential (capability URLs); errors are `{code, message}` (`src/pdf_splitter/errors.py`
has every code), 422s add `errors: [{loc, msg, type}]`.

```
POST   /api/jobs                             multipart file [+ mode=chapters|ranges] → 201 {id, state}
                                              429 rate_limited + Retry-After (PDFSPLIT_RATE_PER_HOUR attempts per client per
                                              sliding hour) · 503 disk_full (under PDFSPLIT_MIN_FREE_GB free on the jobs volume)
GET    /api/jobs/{id}                        {id, state, kind, progress, total, queue_position (1 = next of its kind,
                                              null unless queued), message, error_code (failed only), expires_at,
                                              seconds_left, filename, pages}
                                              (seconds_left: until expires_at by the server's clock — the SPA counts
                                              down from it; only the 410 below declares a job gone)
                                              404 not_found · 410 expired (deleted or past its TTL)
GET    /api/jobs/{id}/analysis               the Analysis (409 not_ready before review)
GET    /api/jobs/{id}/plan                   the saved Plan
PUT    /api/jobs/{id}/plan                   Plan → 200 normalized Plan · 422 invalid · 409 busy while a job runs;
                                              from done (or a failed cut) the job returns to review
POST   /api/jobs/{id}/cut                    202 {id, state: "queued"} (from review, done or a failed cut) · 409 busy/not_ready · 422 empty plan
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
| `PDFSPLIT_RATE_PER_HOUR` | `6` | upload attempts per client per sliding hour (the 7th is 429 with `Retry-After`) |
| `PDFSPLIT_MIN_FREE_GB` | `2` | uploads are refused (503 `disk_full`) below this much free space on the jobs volume (decimal GB) |
| `PDFSPLIT_TRUSTED_PROXY` | unset | the one peer address whose `X-Forwarded-For` (last hop) names the client; unset, the peer is the client |
| `PDFSPLIT_IP_SALT` | unset | secret under the daily-rotating IP hash; set it when more than one api process shares `jobs.db` |
| `PDFSPLIT_MAX_OUTPUT_BYTES` | `2147483648` | ceiling on what one cut may write (the budget is this or 10× the upload, whichever is smaller, never under 256 MB, never over the sandbox's 1 GiB file limit less 64 MiB for the ZIP — so the default lands on 960 MiB) |
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
precheck; it should match `PDFSPLIT_MAX_BYTES`. Routes are `/` (the home page: **Split by chapters** and **Split by
page ranges**, each with its own drop zone), `/j/<id>` (a job), `/privacy` and `/terms`, so whatever serves
`web/dist/` in production must answer `index.html` for all of them.

**Page ranges** (ADR-009): the split mode is chosen once, at upload — the second drop zone sends `mode=ranges` with
the file (`POST /api/jobs`, default `chapters`) and the analyze step writes an empty `source: "ranges"` plan
instead of the chapter suggestion. From then on the saved plan's source is the mode: a job's URL is just `/j/<id>`,
a query string on it is ignored, and opening a job never changes it. The review screen is the range field (`1-10, 15-20, 40` — ranges may overlap or leave pages out, each bad entry
gets its own error and the plan only follows an error-free text), an "every N pages" fill, and the section list
with rename and delete; no source picker, preview or layout panel. Each section carries `endPage` (inclusive);
the cut copies the whole-page spans with PyMuPDF into the same ZIP + manifest shape (flags empty). `endPage` is
refused on any other source, and overrides on a `ranges` plan.

Once a chapter job reaches `review`, `/j/<id>` shows the plan editor: the source picker (outline level, detected headings by
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

## Deploy

One `docker compose` stack (Architecture ADR-008): **caddy** (TLS, the built SPA, `/api` proxied to the api), **api**
and **worker** — the last two are one image (`Dockerfile` target `python`, non-root uid 10001) with different
commands — sharing the `jobs` volume at `/jobs`. The worker has no network, a read-only root, all capabilities
dropped, a tmpfs `/tmp` and a memory limit; the api is the same minus the network (it is reachable from caddy only;
nothing publishes 8000). The `web` stage builds `web/dist` with Bun and the `caddy` stage copies it to `/srv`. The
engine comes from its public GitHub tag during the build — no LAN access is needed anywhere.

```bash
cp deploy/.env.example deploy/.env       # set PDFSPLIT_IP_SALT (required) and PUBLIC_HOST
docker compose -p pdfsplit -f deploy/compose.yaml up -d --build
docker compose -p pdfsplit -f deploy/compose.yaml logs -f
docker compose -p pdfsplit -f deploy/compose.yaml down          # keep -v off: the volume holds live jobs
```

Always pass `-p`: the file sets `name: pdfsplit`, but an explicit project name is what keeps two stacks on one
host (a real one and a smoke run) apart. `deploy/.env` is read from the compose file's directory:

| Variable | Default | Meaning |
|----------|---------|---------|
| `PDFSPLIT_IP_SALT` | — (required) | the secret under the daily-rotating IP hash (`openssl rand -hex 32`) |
| `PUBLIC_HOST` | `localhost` | Caddy's site address: a public hostname gets a Let's Encrypt certificate (DNS must point at the host and 80/443 must be reachable); `localhost` gets one from Caddy's internal CA |
| `PDFSPLIT_PUBLIC_URL` | `https://localhost` | the site's public base URL, handed to the api |
| `PDFSPLIT_HTTP_PORT` / `PDFSPLIT_HTTPS_PORT` | `80` / `443` | the host ports caddy publishes |
| `PDFSPLIT_SUBNET` / `PDFSPLIT_CADDY_IP` | `172.30.0.0/24` / `172.30.0.10` | the stack's own network and caddy's fixed address on it, which is also the api's `PDFSPLIT_TRUSTED_PROXY` — the one peer whose `X-Forwarded-For` names the client |
| `PDFSPLIT_WORKERS`, `PDFSPLIT_RATE_PER_HOUR`, `PDFSPLIT_MAX_BYTES`, `PDFSPLIT_TTL_HOURS`, `PDFSPLIT_MIN_FREE_GB` | as in § Configuration | passed through to both api and worker |
| `PDFSPLIT_TAG` | `local` | the image tag (`pdfsplit-app:<tag>`, `pdfsplit-caddy:<tag>`) |

Caddy caps request bodies at 210 MB (just above the api's 200 MiB, so an oversized upload still gets the api's own
`too_large` answer), compresses with zstd/gzip, answers `index.html` for every SPA route, and sends
`Content-Security-Policy: default-src 'self'; img-src 'self' blob:`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer` and `X-Frame-Options: DENY`. Certificates live in the `caddy_data` volume.
uvicorn runs with `proxy_headers=False`: a forwarded address is believed only by the api's own trusted-proxy rule.

**Smoke test** — `./deploy/smoke.sh` needs docker compose, curl and the dev environment (uv, for the fixture book).
It builds and starts the stack under the throwaway project `pdfsplit-smoke` (its own image tag, subnet
`172.31.0.0/24`, host ports 18080/18443, the api on `127.0.0.1:18000` for one check, a random salt, 2 uploads per
hour), then drives the synthetic two-column book through caddy: health, the SPA and its headers, upload → review →
`GET`/`PUT` plan → cut → `result.zip` with three PDFs and a manifest, three uploads straight to the api with spoofed
`X-Forwarded-For` (the third must be 429 and the `rate` rows gain at most one client hash), `DELETE` → 410. It
always tears down with `down -v`, untags its images, and fails if anything of the project is left or any other
container on the host changed. Override `SMOKE_PROJECT`, `SMOKE_HTTP_PORT`, `SMOKE_HTTPS_PORT`, `SMOKE_API_PORT`,
`SMOKE_SUBNET`, `SMOKE_CADDY_IP` if those collide.
