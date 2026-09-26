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
| `PDFSPLIT_TRUSTED_PROXY` | unset | the peer addresses (comma-separated; IPv6 in any spelling) whose `X-Forwarded-For` (last hop) names the client; unset, the peer is the client |
| `PDFSPLIT_IP_SALT` | unset | secret under the daily-rotating IP hash; set it when more than one api process shares `jobs.db` |
| `PDFSPLIT_MAX_UPLOADS` | `4` | uploads the api streams at once; beyond it `POST /api/jobs` is 503 `overloaded` (with `Retry-After`) before the body is read |
| `PDFSPLIT_LIMIT_CONCURRENCY` | `64` | uvicorn's ceiling on open connections (its own 503 beyond it) |
| `PDFSPLIT_MAX_UPLOADS_PER_CLIENT` | `2` | of those uploads, how many one client (an IPv4 address or an IPv6 /64) may hold at once; beyond it 429 `rate_limited` (`Retry-After: 5`) before the body is read, no rate slot spent |
| `PDFSPLIT_MIN_UPLOAD_RATE` | `32768` | an upload must deliver at least this many bytes in every 30 s window or it is abandoned with 408 `too_slow` — a floor on the rate, not a time limit |
| `PDFSPLIT_MAX_JSON_BYTES` | `4194304` | largest body of any other request (a plan), in bytes; 413 beyond it, 411 without a `Content-Length` |
| `PDFSPLIT_BODY_TIMEOUT` | `20` | seconds such a body has to arrive whole before the request is answered 408 `too_slow` |
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
commands — sharing the `jobs` volume at `/jobs`. Two networks: `edge` (caddy alone: the published ports, the way
out to the ACME servers) and `backend` (`internal`: caddy and the api; Docker forwards nothing for it, so the api
can resolve nothing and reach neither the internet nor the LAN); the worker has no network at all. One thing
`internal` does **not** block: the host itself, which sits on the backend network as its gateway
(`172.30.0.1` / `fd30:5eaf:9a13::1`) — a service the host binds to `0.0.0.0`/`[::]` (sshd, say) answers the api
there unless the host's firewall drops it, so the VM needs the rule under **Host firewall** below. Both networks
carry IPv4 and an IPv6 ULA /64, so an IPv6 visitor is DNAT'd to caddy natively and reaches the api as their own
address (rate-limited per /64). The worker has a read-only root, all capabilities dropped, a 64 MB tmpfs `/tmp` and
a memory limit with no swap; the api is the same. Uploads spool under `/jobs/.spool` on the volume, never in RAM,
and `POST /api/jobs` refuses what it can from the headers alone (an oversized `Content-Length`, a fifth concurrent
upload, a client's third, a spent rate window) before it reads a byte of body; an upload that then falls under
32 KiB per 30 s is abandoned (408), and any other body (a plan) must be declared, at most 4 MiB and complete
within 20 s before its route runs. The `web` stage builds `web/dist` with Bun and the `caddy` stage copies it to
`/srv`. The engine comes from its public GitHub tag during the build — no LAN access is needed anywhere.

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
| `PDFSPLIT_SUBNET` / `PDFSPLIT_SUBNET6` | `172.30.0.0/24` / `fd30:5eaf:9a13::/64` | the `backend` network (internal: caddy + api) |
| `PDFSPLIT_CADDY_IP` / `PDFSPLIT_CADDY_IP6` | `172.30.0.10` / `fd30:5eaf:9a13::10` | caddy's fixed addresses on `backend`; together they are the api's `PDFSPLIT_TRUSTED_PROXY` — the only peer whose `X-Forwarded-For` names the client (caddy may connect over either family) |
| `PDFSPLIT_EDGE_SUBNET` / `PDFSPLIT_EDGE_SUBNET6` | `172.30.1.0/24` / `fd30:5eaf:9a13:1::/64` | the `edge` network (caddy alone: published ports, ACME) |
| `PDFSPLIT_WORKERS`, `PDFSPLIT_RATE_PER_HOUR`, `PDFSPLIT_MAX_BYTES`, `PDFSPLIT_TTL_HOURS`, `PDFSPLIT_MIN_FREE_GB`, `PDFSPLIT_MAX_UPLOADS`, `PDFSPLIT_LIMIT_CONCURRENCY`, `PDFSPLIT_MAX_UPLOADS_PER_CLIENT`, `PDFSPLIT_MIN_UPLOAD_RATE`, `PDFSPLIT_MAX_JSON_BYTES`, `PDFSPLIT_BODY_TIMEOUT` | as in § Configuration | passed through to both api and worker |
| `PDFSPLIT_TAG` | `local` | the image tag (`pdfsplit-app:<tag>`, `pdfsplit-caddy:<tag>`) |

Caddy caps request bodies at 210 MB (just above the api's 200 MiB, so an oversized upload still gets the api's own
`too_large` answer), compresses with zstd/gzip, answers `index.html` for every SPA route, and sends
`Content-Security-Policy: default-src 'self'; img-src 'self' blob:`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer` and `X-Frame-Options: DENY`. Certificates live in the `caddy_data` volume.
uvicorn runs with `proxy_headers=False`: a forwarded address is believed only by the api's own trusted-proxy rule.
The api listens on IPv4 inside its container; caddy resolves `api` to both families and falls back to v4 at once
when the v6 dial is refused, so the v6 entry in `PDFSPLIT_TRUSTED_PROXY` costs nothing and covers a dual-stack
listener later.

**Host firewall.** Docker's own rules live in the FORWARD chain; what the api sends to the host's own address on
the backend network (the gateway) goes through INPUT, which is the host's to police. Drop it there, ahead of every
allow, for both families — with the subnets from `deploy/.env` if you changed them:

```bash
# ufw (Ubuntu): the before-rules run ahead of `ufw allow ...`; put these first in the *filter section's
# ufw-before-input chain of BOTH files, then `ufw reload`.
#   /etc/ufw/before.rules    -A ufw-before-input -s 172.30.0.0/24 -j DROP
#   /etc/ufw/before6.rules   -A ufw-before-input -s fd30:5eaf:9a13::/64 -j DROP
# nftables (Debian without ufw), persisted in /etc/nftables.conf under `chain input`:
#   ip  saddr 172.30.0.0/24 drop
#   ip6 saddr fd30:5eaf:9a13::/64 drop
# Check, from inside the api (expects `blocked` on every line; sshd is the service every VM has):
docker compose -p pdfsplit -f deploy/compose.yaml exec api python -c '
import socket
for host in ("172.30.0.1", "fd30:5eaf:9a13::1"):
    try: socket.create_connection((host, 22), timeout=2).close(); print("REACHED", host)
    except OSError as e: print("blocked", host, type(e).__name__)'
```

**Smoke test** — `./deploy/smoke.sh` needs docker compose, curl and the dev environment (uv, for the fixture book
and the hash arithmetic). It builds and starts the stack under the throwaway project `pdfsplit-smoke` (its own image
tag, subnets `172.27.13.0/24` + `fd27:5eaf:9a13::/64` and `172.27.14.0/24` + `fd27:5eaf:9a13:1::/64`, host ports
18080/18443, a random salt, 6 uploads per hour), then drives the synthetic two-column book through caddy: health,
the SPA and its headers, upload → review → `GET`/`PUT` plan → cut → `result.zip` with three PDFs and a manifest.
Then the checks the gate asked for: the api can reach neither `github.com` nor the host's other bridges nor a public
resolver while caddy reaches the ACME directory, and the host itself is probed at the backend gateway (v4 and v6,
ports `SMOKE_HOST_PORTS`, default `22`) — a **WARNING**, not a failure, when it answers, since only the host's
firewall can fix that; straight at the api from a one-off client on `backend` (`deploy/smoke_client.py`
in the app image — nothing publishes the api, and Docker binds no port on an internal network), a flood of 8 × 300 MiB
uploads is 8 × 413 with under 2 MB of body read each and no slot spent, 6 × 60 MiB at once is 4 spooled to
`/jobs/.spool` + 2 × 503 `overloaded`, and three uploads with spoofed `X-Forwarded-For` are 201 201 429 under the
client's own hash; a client on `edge` uploading to caddy over IPv6 and the host reaching the published port at the
edge network's v6 gateway (the netfilter DNAT path a visitor takes) land in one bucket, their shared /64; four
uploads trickling 256 B/s from one address are 2 × 429 (its cap) + 2 × 408 (abandoned after a 30 s window) while a
second address uploads 201 in the meantime, and 63 `PUT /plan` bodies that never finish are 63 × 408 after 20 s with
the api healthy afterwards; `DELETE` → 410. It always tears down with `down -v`, untags its images, and fails if
anything of the project is left or any other container on the host changed. Override `SMOKE_PROJECT`,
`SMOKE_HTTP_PORT`, `SMOKE_HTTPS_PORT`, `SMOKE_SUBNET`, `SMOKE_SUBNET6`, `SMOKE_CADDY_IP`, `SMOKE_CADDY_IP6`,
`SMOKE_EDGE_SUBNET`, `SMOKE_EDGE_SUBNET6` if those collide, `SMOKE_HOST_PORTS` to probe more of the host.
