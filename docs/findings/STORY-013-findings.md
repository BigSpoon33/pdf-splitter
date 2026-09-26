# Findings — STORY-013
**Date:** 2026-09-26
**Status:** done

Commit `cce9bd1 feat: STORY-013 - containers, compose, Caddy and an end-to-end smoke test` on `feature/mvp`, pushed to
`origin` (GitHub) and `gitea`. Last story of the autonomous run; STORY-014 is held for Shuma (§ Handoff for STORY-014).
**Gate r1** (`docs/findings/STORY-013-review.md`) failed it with 3 confirmed findings + 1 hardening; the fix is the
commit `fix: STORY-013 - gate r1: uploads guarded before the body is read, real IPv6 client addresses, api without
egress, smoke on an empty host` (`6ef0253`) on top of `7d9311a` — § Gate r1 fixes below; `3396e1a` repaired the SPA
parity it broke. **Gate r2** (§ Round 2 of the review) failed it with 5 confirmed findings; the fix is `fix: STORY-013 -
gate r2: slow bodies can't pin the caps, v6 keyed per /64, host reachability documented and probed` on top of
`cc32530` — § Gate r2 fixes below. The file:line references in § AC Verification are as of `cce9bd1`; the gate sections
cite the lines current at their commit.

## AC Verification
- [x] AC-1: one multi-stage `Dockerfile` — `:6` `web` (`oven/bun:1.3.5`, `bun install --frozen-lockfile` + `bun run build`),
  `:15` `python-build` (`python:3.12-slim` + uv 0.12.10 + git, `uv sync --frozen --no-dev` against the copied `uv.lock`,
  `--no-editable` so only the venv is shipped), `:33` `python` = the api AND worker runtime (no git, uid 10001 `app` `:36`,
  `USER app` `:42`, `ENTRYPOINT ["pdf-splitter"]` `:44`, `/jobs` owned by `app` so the named volume inherits it), `:48`
  `caddy` (`caddy:2-alpine` + `deploy/Caddyfile` + `web/dist` → `/srv`). `.dockerignore` keeps `.venv`, `web/node_modules`,
  `web/dist`, `jobs`, `docs`, `tests`, `.git`, `deploy/.env` out of the context. `docker build --no-cache` of every target
  passed: `pdfsplit-app` (python) **295 MB**, `pdfsplit-caddy` **90.1 MB**, the `web` build stage 504 MB (discarded).
  Sanity inside the image: `uid=10001(app)`, `monograph_splitter 0.4.2`, `pymupdf 1.28.2`, `pdf-splitter --help` works.
- [x] AC-2: `deploy/compose.yaml` — `name: pdfsplit` `:6` (orchestrator's isolation note: a bare `docker compose` can never
  default to the directory name `deploy`); `caddy` `:37` publishes `${PDFSPLIT_HTTP_PORT:-80}`/`${PDFSPLIT_HTTPS_PORT:-443}`,
  fixed `ipv4_address: ${PDFSPLIT_CADDY_IP:-172.30.0.10}` `:56` on the stack's own network (subnet `:107`); `api` `:64`
  (`api --host 0.0.0.0 --port 8000`, `expose: 8000` `:68`, nothing published, `PDFSPLIT_TRUSTED_PROXY` = caddy's address
  `:71`, `mem_limit: 2g`, healthcheck on `/api/health` `:76`); `worker` `:84` (`network_mode: none` `:92`, `mem_limit: 3g`
  `:94`); both from the `x-python` anchor: `read_only: true` `:25`, `tmpfs: /tmp` `:26`, `cap_drop: [ALL]` `:29`,
  `no-new-privileges`; volumes `jobs` (both), `caddy_data`, `caddy_config` `:97`. `PDFSPLIT_IP_SALT` is required
  (`${…:?}` `:16`) from `deploy/.env` (gitignored; template `deploy/.env.example`). `docker compose config` renders cleanly.
- [x] AC-3: `deploy/Caddyfile` — `{$PUBLIC_HOST:localhost}` site address `:3`, `encode zstd gzip` `:4`,
  `Content-Security-Policy "default-src 'self'; img-src 'self' blob:"` `:8` + `X-Content-Type-Options nosniff` `:9`
  + `Referrer-Policy no-referrer` + `X-Frame-Options DENY` + `-Server`, `request_body max_size 210MB` `:18`,
  `handle /api/*` → `reverse_proxy api:8000` `:22`, SPA `try_files {path} /index.html` `:28`. `caddy validate` (in
  `caddy:2-alpine`) → `Valid configuration`; `caddy fmt --diff` → no changes. The SPA under the CSP: see § CSP below.
- [x] AC-4: `deploy/smoke.sh` — throwaway project `pdfsplit-smoke` (`-p`, own tag `:smoke`, subnet `172.31.0.0/24`, host ports
  18080/18443, api on `127.0.0.1:18000` via `deploy/compose.smoke.yaml` only, random salt, `RATE_PER_HOUR=2`),
  `trap teardown EXIT` `:57`, `up -d --build --wait` `:69`, health through caddy `:73`, SPA + fallback + headers + gzip,
  upload `:95`, poll to `review` `:108`, `GET`/`PUT` plan `:115`, `POST /cut` `:119`, poll to `done` `:120`, `result.zip`
  `:123`, 3 PDFs + manifest asserted `:125`, XFF check `:135-148`, `DELETE` → 204 → 410 `:151-153`, `down -v` + untag +
  leftover/other-container checks `:40-55`. Ran twice for real (transcript below): **SMOKE PASSED**, exit 0.
- [x] AC-5: the engine is fetched only from `https://github.com/BigSpoon33/pdf-splitter-engine?rev=v0.4.2` (`uv.lock:139`;
  `git ls-remote --tags` shows `v0.4.2` = `116a4bb7`); `grep -rn "gumshu\|192\.168\.\|gitea" Dockerfile .dockerignore deploy/
  pyproject.toml uv.lock` → none. `docker build --no-cache` of the `python` target succeeded on this laptop (which CAN reach
  the LAN), so the "no LAN" property is **by construction** (every fetch in the build is `docker.io`, `ghcr.io`, `pypi.org`,
  `github.com`); no non-LAN host was available in the autonomous run — the first STORY-014 build on the VM is the real proof.
- [x] Addendum: `src/pdf_splitter/cli.py:31` `uvicorn.run(..., proxy_headers=False)` (why at `:27-29`), asserted by
  `tests/test_health.py:85` (`test_cli_api_serves_the_app_from_env_settings`); `PDFSPLIT_TRUSTED_PROXY` = caddy's fixed
  compose IP; `PDFSPLIT_IP_SALT` from `deploy/.env`; the smoke's XFF check (below) shows a spoofed header at the api port is
  counted under the real peer.

## Test Results
**Command:** `uv run pytest -q` — **Result:** pass — `427 passed in 90.13s` (427 → 427: the addendum's assertion joined the
existing CLI test rather than adding one). After the gate r1 fix: `434 passed in 92.73s` (§ Gate r1 fixes).
**Command:** `uv run ruff check` — pass — `All checks passed!`; `ruff format --check` on the two touched files — already formatted.
**Command:** `cd web && bun run check` — pass — `337 FILES 0 ERRORS 0 WARNINGS`.
**Command:** `cd web && bun run test` — pass — `Test Files 21 passed (21) · Tests 284 passed (284)`.
**Command:** `cd web && bun run build` — `dist/assets/index-CTEkmUWg.js 109.15 kB │ gzip: 38.89 kB` (unchanged; the image's
own build produced the identical hash).
**Command:** `docker run --rm -v $PWD/deploy/Caddyfile:/etc/caddy/Caddyfile:ro caddy:2-alpine caddy validate --config
/etc/caddy/Caddyfile` — `Valid configuration`.
**Command:** `./deploy/smoke.sh` — **pass** (run 2, after the teardown fix; run 1 identical apart from the teardown line):
```
05:28:54 smoke: project pdfsplit-smoke, caddy https://localhost:18443, api direct http://127.0.0.1:18000, 22 other containers running
05:28:55   ok  fixture book: 127643 bytes
05:28:55 docker compose up -d --build --wait
05:29:10   ok  stack up: api=Up 6 seconds (healthy) caddy=Up Less than a second worker=Up 6 seconds
05:29:10   ok  GET /api/health via caddy → 200 {"ok":true,"queue":0,"disk_free_gb":234.3,"engine_version":"0.4.2"}
05:29:10   ok  SPA: / and /j/<id> serve index.html; CSP + nosniff present; /assets/index-CTEkmUWg.js gzip-encoded
05:29:11   ok  POST /api/jobs → 201 (state queued)
05:29:13   ok  analyze → review (6/6 pages)
05:29:13   ok  GET /plan → 200 (source headings, 3 sections); PUT /plan → 200
05:29:16   ok  POST /cut → 202; cut → done (3/3 sections)
05:29:16   ok  GET /result.zip → 200 application/zip, 94757 bytes, 3 PDFs + manifest.json:
        001 - Foundations of Testing.pdf
        002 - Chapter Two- The Middle of the Synthetic Book.pdf
        003 - Closing Chapter.pdf
05:29:17   ok  XFF check: 3 uploads straight to the api with spoofed X-Forwarded-For → 201 429 429; rate rows 1→2, distinct client hashes 1→1
05:29:17   ok  DELETE → 204, GET → 410 (expired)
05:29:17 teardown: docker compose -p pdfsplit-smoke down -v; untag the :smoke images
05:29:21 other containers untouched: 22 before and after; leftovers of pdfsplit-smoke: 0
05:29:21 SMOKE PASSED
```
**XFF evidence, read:** the caddy upload took slot 1 of the peer's 2/hour window (caddy's client, the docker gateway
`172.31.0.1`, forwarded as the LAST hop from the trusted `172.31.0.10`, resolves to the same address the direct uploads
arrive from). Three direct uploads to `127.0.0.1:18000`, each with a different `X-Forwarded-For` (`203.0.113.1/2/3`): the
first got the last slot (201), the second and third were 429. Had the header been believed (uvicorn's default
`proxy_headers=True`, or the app trusting any peer) each would have opened its own window and all three would be 201. The
`rate` table agrees: 1 → 2 rows (only 201s are recorded) and `count(distinct ip_hash)` stayed 1 — no new client hash.
**Host state:** `docker ps` before and after each run is byte-identical (13 running containers, incl. `deploy-caddy-1` /
`deploy-docs-1` at "Up 23 hours"); `docker ps -a` / `volume ls` / `network ls` / `images` show nothing of `pdfsplit-smoke`,
`pdfsplit-csp` or a `:smoke`/`:csp` tag afterwards.

## CSP: the built SPA under `default-src 'self'; img-src 'self' blob:`
Headless Chromium (Browser skill's Playwright) against a second throwaway stack (`-p pdfsplit-csp`, ports 18081/18444,
subnet `172.31.1.0/24`; torn down after): `/` → upload the fixture through the drop zone → `/j/<id>` → "Split into 3 PDFs"
visible → select section 1 → the sheet PNG loads (`blob:https://localhost:18444/…`, an object URL the SPA makes from the
`/api/.../sheets/N.png` fetch — hence `blob:`) with the SVG overlay → Split → "Download all (ZIP)" + three links → reload of the
deep link renders the same page (`docs/findings/STORY-013-csp-drive.png`). No blocked script or style: the production bundle
is one external module + one stylesheet, and the components' `style=`/`style:` bindings go through the CSSOM.
**One violation, cosmetic:** `web/index.html` carries `<link rel="icon" href="data:,">` (the usual "no favicon request"
trick); Chromium logs `Loading the image 'data:,' violates … "img-src 'self' blob:"` once per page load. The effect is what
the tag intends anyway (no favicon), so the policy was NOT widened. Fix options for STORY-014 (both outside this story's
authority): a real `/favicon.svg` in `web/public/` with `href="/favicon.svg"`, or `img-src 'self' blob: data:`. The other
console line is the expected `409 not_ready` the SPA gets from the manifest before the first cut.

## Gate r1 fixes (2026-09-26, retry)

**1. Uploads can't exhaust the api** (review finding 1, CONFIRMED: 8 × 300 MiB concurrent → Starlette spooled every
part into the api's unsized tmpfs `/tmp` before the route ran → OOMKilled at 2 GiB).
- `src/pdf_splitter/upload.py:75` `UploadGuard`, a pure ASGI layer around the app for `POST /api/jobs` only
  (`app.py:41`, added before the access log so its refusals are logged with an `X-Request-ID`). `:108`
  `refuse_by_headers`: `Content-Length` > `MAX_BYTES` + a 64 KiB multipart envelope (`:43`) → 413 `too_large`; chunked
  (no length, a `Transfer-Encoding`) → 411; a body that is not `multipart/form-data` → 415 (the urlencoded parser would
  buffer it in memory); no body at all passes through, the route keeps answering it `not_pdf`. `:91` the in-flight cap:
  `in_flight >= max_uploads` → 503 `overloaded` + `Retry-After: 5` (`errors.py:28`, a new code — the SPA shows its fallback
  message until `web/` learns it, § Handoff), reserved BEFORE the first await so two requests can't both find room. `:125`
  `admit` (threadpool: sqlite + statvfs) takes the client's rate slot (429 + `Retry-After`, same semantics) and runs the
  disk guard, then hands the hash to the route via `request.state.client_hash` (`:148`); `_accept` (`:241`) reads it and
  never claims a slot — one upload, one `rate` row (`tests/test_upload.py:457`). Every refusal carries `Connection: close`
  (`:152` `refuse`), so uvicorn (h11) closes the socket and the client stops sending. `_copy_capped` stays as the exact
  streaming backstop. Order: size → cap → slot → disk, so a server too busy never spends the visitor's window.
- **Spool on disk:** `upload.py:57` `spooling_to` — the app's lifespan (`app.py:34`) creates `<jobs>/.spool` (`config.py:11`
  `SPOOL`, `:59` `spool_dir`) and points `tempfile.tempdir` + `TMPDIR` at it for as long as the app runs (restored on
  shutdown), so Starlette's `SpooledTemporaryFile` rollover and the preflight/preview children land on the volume. The
  janitor (`janitor.py:88`) never treats `.spool` as an orphan directory; `:97` `_reap_spool` removes files inside it older
  than `ORPHAN_GRACE` (1 h) and counts them under a new `spool` key. Compose sizes both containers' `/tmp` tmpfs at 64 MB
  (`compose.yaml:37`) and adds `memswap_limit` = `mem_limit` (`:92`, `:112`).
- **Concurrency:** `Settings.max_uploads` = 4, `Settings.limit_concurrency` = 64 (`config.py:33-35`,
  `PDFSPLIT_MAX_UPLOADS`/`PDFSPLIT_LIMIT_CONCURRENCY`, passed through in `compose.yaml:21-22`); `cli.py:38`
  `uvicorn.run(..., limit_concurrency=...)` (`tests/test_health.py:87`).
- Tests: `tests/test_upload.py:416` oversized `Content-Length` → 413 with the route never reached, the spool directory
  empty, no `rate` row, `Connection: close` — and just above the cap but within the envelope the streaming copy still
  decides; `:434` chunked → 411, urlencoded/raw → 415, no body → the route's 400, other methods untouched; `:457` the
  slot is taken before the body is read and exactly once; `:472` two uploads held in the route, a third is 503
  `overloaded` with no slot spent, the cap frees when they finish; `:509` `tempfile`/`TMPDIR` point at `<jobs>/.spool`
  while the app runs and are restored after; `tests/test_limits.py:418` the spool directory survives the janitor, stale
  files inside it go after the grace. Two existing burst tests raise `max_uploads` to keep testing the window
  (`test_upload.py:361`, `test_limits.py:157`).

**2. Real IPv6 addresses** (finding 2, CONFIRMED: an IPv4-only network behind docker-proxy on `[::]` turned every v6
visitor into the bridge gateway — one shared rate bucket).
- `compose.yaml:123-133`: both networks `enable_ipv6: true` with a ULA /64 each (`PDFSPLIT_SUBNET6`
  `fd30:5eaf:9a13::/64`, `PDFSPLIT_EDGE_SUBNET6` `fd30:5eaf:9a13:1::/64`); caddy fixed at `PDFSPLIT_CADDY_IP6`
  `fd30:5eaf:9a13::10` on `backend` (`:70`); `PDFSPLIT_TRUSTED_PROXY` = both addresses (`:86`).
  `ratelimit.py:37` `trusted_proxies` parses the comma-separated list and `:28` `canonical` compares
  `ipaddress`-normalised spellings (`fd30:5eaf:9a13:0:0:0:0:10` = `fd30:5eaf:9a13::10`; non-IP strings such as the
  tests' `testclient` compare as written) — `tests/test_limits.py:88`. No daemon change was needed on this laptop
  (Docker 29.7.2, `iptables` backend: `docker network create --ipv6` with a ULA subnet just works).
- The api listens on IPv4 (`--host 0.0.0.0`; uvicorn's `--host ::` would be v6-only because asyncio sets
  `IPV6_V6ONLY`): caddy resolves `api` to A + AAAA, the v6 dial is refused and Go falls back to v4 at once. The v6
  trusted entry costs nothing and is there for a dual-stack listener later.
- Live (smoke run below): a client on `edge` with the fixed address `fd27:5eaf:9a13:1::77` uploading to caddy over v6
  is recorded under exactly `ip_hash("fd27:5eaf:9a13:1::77")`; the host reaching the published port at the edge
  network's v6 gateway (`[fd27:5eaf:9a13:1::1]:18443` — the netfilter DNAT path, not docker-proxy) is recorded under
  exactly `fd27:5eaf:9a13:1::1`. Distinct buckets from the v4 client (`172.27.13.77`). A caveat learned on the way:
  host loopback (`127.0.0.1` AND `[::1]`) goes through the userland proxy, which here dials the container from the
  bridge's v6 gateway — so a loopback `curl -6` is not the representative path and the smoke uses the gateway
  address instead. A remote visitor's source is preserved by DNAT (only the destination is rewritten; MASQUERADE
  applies to docker subnets only), so the VM may publish an AAAA record; if a host's Docker lacked `ip6tables` the
  fallback is simply no AAAA (§ Handoff).

**3. api without egress** (orchestrator hardening; the review refuted it as a story defect).
- `compose.yaml:62-70`: caddy on `edge` (`priority: 100`, so it is the primary network — published ports and the default
  route) and `backend`; the api on `backend` only (`:89`), which is `internal: true` (`:130`); the worker stays
  `network_mode: none`. Docker binds no port on an internal network, so `deploy/compose.smoke.yaml` (the loopback publish
  of the api for the XFF check) was removed; the smoke reaches the api from a one-off client container on `backend`
  (`deploy/smoke_client.py`, run in the app image with a fixed address — `smoke.sh:53-60`).
- Live (smoke): from inside the api, `github.com:443` → name resolution fails (internal networks get no external DNS),
  the host's edge gateway `172.27.14.1:18443` and `1.1.1.1:53` → `Network is unreachable`; caddy → `wget
  https://acme-v02.api.letsencrypt.org/directory` OK; caddy → api health still 200; `docker port pdfsplit-smoke-api-1` empty.

**4. smoke.sh on an empty host** (finding 4, CONFIRMED): `smoke.sh:51` `others()` is
`docker ps -a … | { grep -v "^$PROJECT-" || true; } | sort`. Verified with a stubbed `docker` (a script printing nothing)
under `set -euo pipefail`: the old definition aborts the shell (`exit=1`), the new one yields `[]`, count 0.

**Also:** `smoke.sh` moves `$WORK` to `/var/tmp` (mode 755: the client containers run as uid 10001), pins the smoke to
`172.27.13.0/24` + `172.27.14.0/24` and the ULA `fd27:5eaf:9a13::/64` + `:1::/64`, reads the `rate` table with
`compose exec` instead of `compose run`, and computes expected hashes on the host with the run's salt
(`ratelimit.ip_hash`) so every "counted under X" is an exact match, not a count.

**Tests:** `uv run pytest -q` → **434 passed in 92.73s** (427 → 434); `uv run ruff check` → `All checks passed!`. `web/`
untouched (`bun run check`/`test`/`build` as in § Test Results; the image built the identical `index-CTEkmUWg.js`).
`caddy validate` → `Valid configuration` (Caddyfile unchanged).
**Smoke (`./deploy/smoke.sh`), run 2 — PASSED, exit 0** (run 1 stopped at the XFF step on the `$WORK` mode; fixed):
```
06:28:36 smoke: project pdfsplit-smoke, caddy https://localhost:18443, backend 172.27.13.0/24 + fd27:5eaf:9a13::/64, edge 172.27.14.0/24 + fd27:5eaf:9a13:1::/64, 22 other containers running
06:28:36   ok  fixture book: 127643 bytes
06:28:36 docker compose up -d --build --wait
06:28:47   ok  stack up: api=Up 6 seconds (healthy) caddy=Up Less than a second worker=Up 6 seconds
06:28:47   ok  GET /api/health via caddy → 200 {"ok":true,"queue":0,"disk_free_gb":234.2,"engine_version":"0.4.2"}
06:28:47   ok  SPA: / and /j/<id> serve index.html; CSP + nosniff present; /assets/index-CTEkmUWg.js gzip-encoded
06:28:48   ok  egress: api github.com:443 blocked; 172.27.14.1:18443 blocked; 1.1.1.1:53 blocked; caddy reached acme-v02.api.letsencrypt.org; worker network=none; api publishes nothing
06:28:48   ok  POST /api/jobs → 201 (state queued)
06:28:50   ok  analyze → review (6/6 pages)
06:28:50   ok  GET /plan → 200 (source headings, 3 sections); PUT /plan → 200
06:28:53   ok  POST /cut → 202; cut → done (3/3 sections)
06:28:53   ok  GET /result.zip → 200 application/zip, 94757 bytes, 3 PDFs + manifest.json:
        001 - Foundations of Testing.pdf
        002 - Chapter Two- The Middle of the Synthetic Book.pdf
        003 - Closing Chapter.pdf
06:28:55   ok  flood: 8 × 300 MiB straight at the api → 8 × 413 too_large, at most 1664 KB of body read each, 0 slots spent; api oom_killed=false restarts=0 health=healthy mem=2147483648, 55.41MiB resident
06:29:01   ok  in-flight cap: 6 × 60 MiB at once → 4×400 2×503 (4 streamed to the spool: 4 open /jobs/.spool fds mid-upload; 2 refused unread); rate rows +4 under 172.27.13.77; api oom_killed=false restarts=0 health=healthy mem=2147483648
06:29:04   ok  XFF check: 3 uploads straight at the api with spoofed X-Forwarded-For → 201 201 429; rate rows 5→7, all under 172.27.13.77, no new client hash
06:29:06   ok  IPv6: edge client fd27:5eaf:9a13:1::77 → caddy over v6 → 201, counted under fd27:5eaf:9a13:1::77; host → [fd27:5eaf:9a13:1::1]:18443 (v6 DNAT) → 201, counted under fd27:5eaf:9a13:1::1; distinct client hashes 2→3
06:29:06   ok  DELETE → 204, GET → 410 (expired)
06:29:06 teardown: docker compose -p pdfsplit-smoke down -v; untag the :smoke images
06:29:10 other containers untouched: 22 before and after; leftovers of pdfsplit-smoke: 0
06:29:10 SMOKE PASSED
```
**Reading the flood line:** the review's exact flood (8 × 300 MiB at once, api at `mem_limit` 2g with no swap) now ends in
0.7 s with eight 413s; the client (`smoke_client.py` reads the response while it sends) saw the answer after at most
1.6 MB of body per connection — what fitted in socket buffers before the close; the api stayed healthy at 55 MiB RSS,
never restarted, and the `rate` table did not grow. **The cap line:** with `PDFSPLIT_MAX_UPLOADS=4`, six 60 MiB bodies
throttled to overlap → the first four streamed (mid-upload, `ls -l /proc/1/fd` in the api showed 4 open files under
`/jobs/.spool/` — Linux `O_TMPFILE`, hence no names in the directory) and ended `not_pdf`; two got 503 `overloaded` at
once (131 KB sent). **Host state:** `docker ps` before/after each run is byte-identical (13 running containers incl.
`deploy-caddy-1`/`deploy-docs-1`); nothing of `pdfsplit-smoke`, `pdfsplit-r2` (the exploration stack, torn down) or a
`:smoke`/`:r2` tag remains; no image was pushed, no daemon setting changed.

**Addendum — attempt 2b (web parity regression from `6ef0253`):** the gate r1 commit added `overloaded` to
`src/pdf_splitter/errors.py` without a SPA message, so `web/src/lib/errors.test.ts` (which parses errors.py) failed 2 of
285. Fix: `overloaded` → "The service is busy right now — try again in a few seconds." in `web/src/lib/errors.ts`, and
the parser sanity count 16 → 17 in `errors.test.ts`. DropZone already renders `ApiError.userMessage` in place, so no
component change; `JobErrorCode` enumerates worker job failures only, not API codes, so it is unchanged. Checks:
`bun run check` 0 errors 0 warnings (337 files), `bun run test` 285 passed (21 files), `bun run build` ok
(109.22 kB / 38.92 kB gzip), `uv run pytest -q` 434 passed, `uv run ruff check` clean.

## Gate r2 fixes (2026-09-26)

**1. Slow bodies can't pin the caps** (finding 1, CONFIRMED live: 4 trickled uploads held all 4 slots; 63 held `PUT /plan`
bodies filled `limit_concurrency` — no time or rate bound anywhere). No wall-clock cap anywhere: a genuine slow line
still finishes a 200 MiB file.
- **Uploads — the watchdog:** `src/pdf_splitter/upload.py:81` `Progress(min_rate, window, clock)` — `tick(n)` counts
  bytes and, once a window has closed, is False when fewer than `min_rate` arrived in it; `remaining()` is what the wait
  is bounded by. `UploadGuard.stream` (`:157`) runs the app with `receive` wrapped: `asyncio.wait_for(receive(),
  progress.remaining())`, a timeout is `tick(0)` (an empty window is the same failure), a chunk is `tick(len)`. On a
  stalled window the guard sends 408 `too_slow` ITSELF (the parser is mid-body and has sent nothing; `refuse` adds
  `Connection: close`), hands the parser an `http.disconnect` and drops the 400 FastAPI answers to that
  (`guarded_send`, `:181`). `Settings.min_upload_rate` = 32 KiB (`config.py:41`, `PDFSPLIT_MIN_UPLOAD_RATE`) per
  `UPLOAD_WINDOW` = 30 s (`upload.py:50`). The clock is injectable (`UploadGuard(app, settings, clock=)`).
- **Uploads — the per-client cap:** `upload.py:130-140` — before the server's cap admits, the client's key is
  `ratelimit.ip_hash(client_ip)` (the same hash the window uses, so the cap and the window name the same client; held
  in memory only, while the upload streams) and `per_client[key] >= Settings.max_uploads_per_client` (2 of the 4,
  `config.py:38`, `PDFSPLIT_MAX_UPLOADS_PER_CLIENT`) is 429 `rate_limited` + `Retry-After: 5`, unread, no slot spent.
  Order: headers → server cap (503) → client cap (429) → slot → disk. `admit` now takes the address it was handed
  (`:206`) instead of computing it twice.
- **Every other body — `BodyGuard`:** new `src/pdf_splitter/body_guard.py` (`app.py:42`, beside `UploadGuard`, both
  inside the access log). For any http request that is not the upload: no `Content-Length` + a `Transfer-Encoding` →
  411; `Content-Length` > `Settings.max_json_bytes` (4 MiB, `config.py:45`) → 413 `invalid` "The request body is too
  large."; otherwise the body is read WHOLE here under `asyncio.wait_for(receive(), deadline − now)` with the deadline
  `Settings.body_timeout` (20 s, `config.py:46`) from the request → 408 `too_slow` when it is not complete in time;
  a disconnect mid-body ends the request with no answer and no route; what arrived is replayed to the route
  (`replayed`, `:80`). Reading it here changes nothing about memory: the route read the same bytes into memory anyway.
  A byte count backstops the declared length (`:71`). No body at all (a GET, `POST /cut`, `DELETE`) passes untouched.
- `errors.py:30` `too_slow` (408, `upload.py:STATUS`), `web/src/lib/errors.ts:20` the SPA's message
  ("The upload stalled and was abandoned…"), `errors.test.ts` sanity 17 → 18. DropZone renders it in place like every
  upload error.
- **Tests (no real sleeps — the clock is injected):** `tests/test_upload.py:624` an ASGI harness (`Sink` = the parser
  stand-in, `trickling` = a `receive` that advances a fake clock per chunk and blocks past a `None`): `:689` 40 KiB every
  10 s streams through three windows untouched (201, every byte); `:701` 100 bytes in 31 s → 408 sent by the guard with
  `Connection: close`, the parser disconnected and its 201 dropped, in-flight counts freed, the slot spent (a trickler
  pays with their own hour); `:718` the wait's own timeout path on the real clock (a 50 ms window, `min_upload_rate=1`:
  the first window passes on 100 bytes, the empty one refuses). `:567` the per-client cap: two uploads from one /64
  (`2001:db8:1:2::1/::2`) held in the route, a third from `::3` is 429 `rate_limited` + `Retry-After: 5` + `Connection:
  close` with `rate` rows still 2, while `203.0.113.5` gets 201 (two of four slots free); after release the /64 is welcome
  again. `tests/test_body_guard.py` (new): TestClient — chunked → 411, 4 MiB + 1 → 413 `invalid`, both unread (counter),
  no-body requests untouched, a small body reaches the route whole (`404 not_found` proves the lookup ran; the counter
  saw exactly the body); harness with a fake clock — the body replayed in order, a chunk landing after the 20 s bound →
  408 + `Connection: close` with the route never run, the real-clock timeout path (50 ms), a disconnect mid-body → no
  answer, headers-only refusals (chunked/oversized/malformed), a dishonest `Content-Length` capped by what arrives, the
  upload path left to `UploadGuard`. `tests/test_config.py` covers the four new defaults and env names.
- **Live** (smoke transcript below): 4 × 64 KiB at 256 B/s from `172.27.13.78` → `2×429 2×408` (the two admitted
  abandoned after one 30 s window with ≤ 7 KB in), and `172.27.13.79` uploaded 201 while they held; 63 × `PUT /plan`
  declaring 100 KB and sending 100 B, straight at the api → 63 × 408 after the 20 s bound, health through caddy 200
  right after, api `restarts=0 health=healthy`.

**2. v6 keyed per /64** (finding 2, CONFIRMED). `ratelimit.py:54` `rate_key(address)`: IPv4 as is; IPv6 → its /64
(`ipaddress.ip_network((addr, 64), strict=False)` → `2001:db8:1:2::/64`); a v4-mapped v6 address → its v4; a non-IP
string as written. `ip_hash` (`:70`) hashes `rate_key(ip)`, so every consumer — the window, `jobs.ip_hash`, the
per-client cap, the smoke's `hash_of` — agrees. The /128 distinctness assertion in `tests/test_limits.py` is replaced by
`:105` `test_ipv6_clients_share_a_window_per_64_and_v4_mapped_addresses_are_their_v4`: same /64 (three spellings) → one
hash, a different /64 → another, the mapped v4 → the v4's, distinct v4 distinct. Live: the edge client
`fd27:5eaf:9a13:1::77` and the host at `fd27:5eaf:9a13:1::1` now share one bucket (rows 12→13, hashes 4→4, rate_key
`fd27:5eaf:9a13:1::/64`), where gate r1 had counted them apart. The smoke's v6 step asserts that pair; it no longer
asserts a "+1 hash" for the edge client, because the run's very first upload (`curl https://localhost`) reaches caddy
through docker-proxy from the edge gateway — and whether curl picked `::1` or `127.0.0.1` decides whether that opened
the v6 /64's bucket or the v4 gateway's.

**3. Host reachability** (finding 3, CONFIRMED live: Ollama on this laptop answered the api at the backend gateway).
The truth, now in README § Deploy, Architecture ADR-005 ("The host is the exception") and the compose header: `internal`
stops Docker *forwarding* for the network — the api still reaches nothing on the internet or the LAN — but the host sits
on it as the gateway (`172.30.0.1` / `fd30:5eaf:9a13::1`), Docker's rules live in FORWARD, and INPUT is the host's, so
a service bound to `0.0.0.0`/`[::]` answers the api unless the host's firewall drops INPUT from the backend subnets.
`deploy/smoke.sh:154` probes the BACKEND gateway, v4 and v6, on `SMOKE_HOST_PORTS` (default `22`) from inside the api and
prints a **WARNING** (never a failure — the fix is outside the stack) naming the reachable ports and the rule; this run,
with `SMOKE_HOST_PORTS="22 11434"`: `WARN the api reaches the host at its backend gateway: [172.27.13.1]:11434
[fd27:5eaf:9a13::1]:11434`. README § Deploy "Host firewall" has the exact lines (ufw `before.rules`/`before6.rules`
`-A ufw-before-input -s <subnet> -j DROP` for both families; nftables `ip saddr … drop` / `ip6 saddr … drop`) and the
check to run from inside the api afterwards; § Handoff for STORY-014 repeats them for the VM.

**4. Real "before the body" tests** (finding 4, reviewer-proven: `spool_dir.iterdir()` cannot see Starlette's unnamed
`O_TMPFILE`). `tests/test_upload.py:418` `Reading`, an ASGI wrapper around the whole app that counts the body messages
and bytes the app takes from `receive`; `reading_client(settings)`; `assert_unread`. `:446` (oversized `Content-Length`)
and `:489` (the slot before the body) assert the counter is at zero instead of listing the directory — and `:489` first
shows the counter DOES move on the accepted upload (≥ 1 message, more bytes than the PDF) before zeroing it. `:509`
`test_the_reading_counter_catches_a_guard_that_drains_the_body_first` proves the instrument: a guard monkeypatched to
drain the body before refusing moves the counter (1 message, > 2 MiB) and `assert_unread` fails with "read … bytes of
body". The body-guard tests use the same counter.

**5. Docs** (finding 5). § Handoff below no longer asks STORY-014 for the `overloaded` message (done in `3396e1a`).
Architecture § API Interface `201:` line now lists `408 too_slow · 411 invalid (chunked) · 413 too_large|too_many_pages ·
415 invalid (not multipart) · … · 503 disk_full|overloaded`, says which 429 is which (the hour vs. the per-client cap)
and exactly which answers spend a slot (the slot is taken once the headers pass and both caps admit: 201, the route's
400/413, 503 `disk_full` and 408 `too_slow` count; 411/415, a 413 from the headers, 503 `overloaded` and the cap's 429 do
not), plus the body-guard line under `PUT /plan`. § api gained the watchdog and **Body guard** bullets; ADR-007 an "As
built (gate r2)" line for `rate_key`; ADR-008 the per-/64 note. README § Configuration has the four new variables, §
Deploy the corrected reachability prose, the firewall block and the smoke's new steps; `deploy/.env.example` and
`compose.yaml` pass the four through.

**Smoke changes on the way:** the in-flight-cap step could no longer be driven from one address (the per-client cap
refuses four of six first), so it is two steps — the client's cap (6 from `CLIENT4` → `2×400 4×429`, then 2 more so
`CLIENT4` arrives at the XFF check with exactly two slots left, as before) and the server's cap (6 at once from three
addresses `.80/.81/.82`, two each → `4×400 2×503`, 4 open `/jobs/.spool` fds mid-upload). `smoke_client.py` learned
`--method`, `--raw` (a bare JSON body), `--chunk` (a real trickle: the old `--rate` throttled 64 KiB chunks), `--declare`
(promise N bytes, send fewer — a body that never finishes) and `--wait`. One-off client names carry `$BASHPID`: the
`$RANDOM` streams of background subshells are copies of each other and two clients collided on a name.

**Tests:** `uv run pytest -q` → **450 passed in 98.38s** (434 → 450); `uv run ruff check` → `All checks passed!`.
`cd web && bun run check` → `337 FILES 0 ERRORS 0 WARNINGS`; `bun run test` → `Test Files 21 passed · Tests 286 passed`
(285 → 286: the `too_slow` parity case); `bun run build` → `dist/assets/index-DGwoLPx7.js 109.31 kB │ gzip: 38.95 kB`.
`docker compose config` renders the four new variables for api and worker. **Smoke (`SMOKE_HOST_PORTS="22 11434"
./deploy/smoke.sh`), run 4 — PASSED, exit 0** (runs 1–3 stopped at the cap step, a client-name collision and the v6
"+1 hash" assertion — all three smoke-script defects, fixed as described above):
```
08:05:13 smoke: project pdfsplit-smoke, caddy https://localhost:18443, backend 172.27.13.0/24 + fd27:5eaf:9a13::/64, edge 172.27.14.0/24 + fd27:5eaf:9a13:1::/64, 22 other containers running
08:05:13   ok  fixture book: 127643 bytes
08:05:13 docker compose up -d --build --wait
08:05:24   ok  stack up: api=Up 6 seconds (healthy) caddy=Up Less than a second worker=Up 6 seconds
08:05:24   ok  GET /api/health via caddy → 200 {"ok":true,"queue":0,"disk_free_gb":233.9,"engine_version":"0.4.2"}
08:05:24   ok  SPA: / and /j/<id> serve index.html; CSP + nosniff present; /assets/index-DGwoLPx7.js gzip-encoded
08:05:24   ok  egress: api github.com:443 blocked; 172.27.14.1:18443 blocked; 1.1.1.1:53 blocked; caddy reached acme-v02.api.letsencrypt.org; worker network=none; api publishes nothing
08:05:25 WARN  the api reaches the host at its backend gateway: [172.27.13.1]:11434 [fd27:5eaf:9a13::1]:11434 — drop INPUT from 172.27.13.0/24 and fd27:5eaf:9a13::/64 on the host (README § Deploy) before going public
08:05:25   ok  POST /api/jobs → 201 (state queued)
08:05:27   ok  analyze → review (6/6 pages)
08:05:27   ok  GET /plan → 200 (source headings, 3 sections); PUT /plan → 200
08:05:30   ok  POST /cut → 202; cut → done (3/3 sections)
08:05:30   ok  GET /result.zip → 200 application/zip, 94757 bytes, 3 PDFs + manifest.json:
        001 - Foundations of Testing.pdf
        002 - Chapter Two- The Middle of the Synthetic Book.pdf
        003 - Closing Chapter.pdf
08:05:32   ok  flood: 8 × 300 MiB straight at the api → 8 × 413 too_large, at most 1728 KB of body read each, 0 slots spent; api oom_killed=false restarts=0 health=healthy mem=2147483648, 56.09MiB resident
08:05:44   ok  per-client cap: 6 × 60 MiB at once from 172.27.13.77 → 2×400 4×429 (2 streamed, 4 refused unread, no slot); rate rows +2 under 172.27.13.77; then 2 more → 2 × 400
08:05:50   ok  server cap: 6 × 60 MiB at once from 3 addresses → 4×400 2×503 (4 streamed to the spool: 4 open /jobs/.spool fds mid-upload; 2 refused unread); rate rows +4 across 2 new hashes; api oom_killed=false restarts=0 health=healthy mem=2147483648
08:05:53   ok  XFF check: 3 uploads straight at the api with spoofed X-Forwarded-For → 201 201 429; rate rows 9→11, all under 172.27.13.77, no new client hash
08:05:55   ok  IPv6: edge client fd27:5eaf:9a13:1::77 → caddy over v6 → 201, counted under its /64; host → [fd27:5eaf:9a13:1::1]:18443 (v6 DNAT) → 201, same /64 → the same bucket (rows 12→13, hashes 4→4; rate_key fd27:5eaf:9a13:1::/64)
08:06:26   ok  slow uploads: 4 × 64 KiB at 256 B/s from 172.27.13.78 → 2×408 2×429 (2 refused by the per-client cap unread, 2 abandoned after a 30 s window with ≤ 7 KB in); 172.27.13.79 got 201 meanwhile; rate rows +3 (2 admitted trickles + 1), 2 new hashes; api oom_killed=false restarts=0 health=healthy mem=2147483648
08:06:47   ok  held PUTs: 63 × PUT /plan declaring 100 KB and sending 100 B, straight at the api → 63 × 408 too_slow after the 20 s bound; health via caddy 200 after; api oom_killed=false restarts=0 health=healthy mem=2147483648
08:06:47   ok  DELETE → 204, GET → 410 (expired)
08:06:47 teardown: docker compose -p pdfsplit-smoke down -v; untag the :smoke images
08:06:49 other containers untouched: 22 before and after; leftovers of pdfsplit-smoke: 0
08:06:49 SMOKE PASSED
```
**Isolation:** `docker ps -a`, `docker network ls` and `docker volume ls` before and after the four runs are identical
(22 containers, incl. `deploy-*` and `multica-*` on `172.26.0.0/16`, untouched); no `pdfsplit*` image or `:smoke` tag
remains; nothing pushed, no daemon setting changed, the smoke's own subnets stayed `172.27.13/14.0/24` + the `fd27:` ULAs.

## Bugs Found
- **`docker compose down --rmi all` under a throwaway project untagged another project's image.** First teardown design.
  The smoke build is byte-identical to a `:local` build, so both tags share one image ID and compose's remove-by-ID took
  `pdfsplit-app:local` with it (observed while tearing down `pdfsplit-csp`). On the VM that would untag the running
  stack's image (containers keep running; the next `up --build` re-tags). Fixed before the commit: teardown is `down -v`
  plus `docker image rm pdfsplit-app:smoke pdfsplit-caddy:smoke` (`smoke.sh:43-46`), which only untags; proven with
  `docker tag a:local a:x && docker image rm a:x` → `a:local` intact.
- None in the api/worker/SPA: the stack ran the e2e contract (`tests/test_api_e2e.py`) unchanged on the first try.

## Decisions
- **`name: pdfsplit` + always `-p`** (orchestrator note): README and the compose header say so; `smoke.sh` and the docs
  never invoke compose without `-p`.
- **Two-stage Python image:** `python-build` (uv + git) → `python` (venv only). Keeps git and uv out of the runtime image
  and lets `--no-editable` ship the package in site-packages; `UV_COMPILE_BYTECODE=1` + `PYTHONDONTWRITEBYTECODE=1` so a
  read-only root never wants to write `__pycache__`.
- **Fixed caddy address via env with defaults** (`PDFSPLIT_SUBNET`/`PDFSPLIT_CADDY_IP`, `172.30.0.0/24`/`.10`): the address
  is also the api's `PDFSPLIT_TRUSTED_PROXY`, so the two are one variable. `172.30` is free on this laptop (docker uses
  `172.17-172.28`); the smoke uses `172.31.0.0/24` so it can run beside a real stack.
- **TLS locally:** the `localhost` default keeps HTTPS on (Caddy's internal CA) rather than `auto_https off`, so the local
  stack behaves like the VM (redirect, HSTS-free, same headers); the smoke uses `curl -k`. Caddy logs a warning that it
  cannot install its root CA into the container's trust store — harmless.
- **Caddy ports default to 80/443** in the file (AC-2) and are env-overridable; on this laptop only the smoke/csp runs
  bound them (18080/18443, 18081/18444), never the defaults (orchestrator addendum).
- **`mem_limit`** 2g (api: previews are 2 GB-AS subprocesses but RSS is small) / 3g (worker: two sandboxed tasks) — a
  4 GB VM with both at their cap would swap; STORY-014 may lower `PDFSPLIT_WORKERS` to 1 on a 4 GB box.
- **Healthcheck without curl:** the image has none; the venv's python fetches `/api/health` and looks for `"ok":true`.
- **Worker `depends_on: api`** (started, not healthy) only orders the start; the worker needs the volume, not the api.
- **The smoke's XFF assertion** is the `429` on the third spoofed upload plus the `rate` table's distinct-hash count read by
  `compose run --rm --no-deps --entrypoint python api` on the volume (no new API surface, no host sqlite3 needed). Both
  hold whether or not the caddy path's client resolves to the same peer as the direct path (it does here). *Superseded by
  § Gate r1 fixes:* the direct uploads now come from a fixed-address client on `backend`, the table is read with
  `compose exec`, and each hash is matched exactly against `ratelimit.ip_hash(address, secret=salt)`.
- **The fixture book** is built on the host by the dev env (`tests/fixtures/books.py`), not baked into `deploy/`: the smoke
  already needs the repo, and the image must not carry tests.
- **caddy image target** uses `caddy:2-alpine` (the same image `caddy:2` points at; already on this laptop).

## Handoff Context for Next Session
The stack is exactly what the VM should run: `cp deploy/.env.example deploy/.env`, set `PDFSPLIT_IP_SALT` and
`PUBLIC_HOST=<domain>` (+ `PDFSPLIT_PUBLIC_URL=https://<domain>`), then `docker compose -p pdfsplit -f deploy/compose.yaml
up -d --build`. `./deploy/smoke.sh` is the local regression for any compose/Caddyfile change and must stay green; it needs
uv on the host for the fixture. `deploy.sh` (ADR-008) and `PROVISION.md` are STORY-014's.

## Handoff for STORY-014 (held for Shuma)
Needed from Shuma before anything runs (PRD Q-1/Q-2/Q-4):
- **Domain** (Q-1): registered, with DNS access; an `A` record → the VM's public IP BEFORE the first `up`, or Caddy's ACME
  order fails and retries with back-off. An `AAAA` record is fine too (gate r1): the compose networks carry IPv6, Docker
  DNATs a v6 visitor to caddy's own v6 address and the api sees their real address — provided the VM's Docker has
  `ip6tables` on (the default since Docker 27; this laptop's 29.7.2 needed no daemon change). If it does not (or the VM
  has no v6), publish no AAAA: every v6 visitor would otherwise share one rate bucket via docker-proxy.
- **Host account** (Q-2): Hetzner or DigitalOcean, region; a 2 vCPU / 4 GB box is the floor (`PDFSPLIT_WORKERS=1` there),
  4 vCPU / 8 GB comfortable. Ubuntu LTS / Debian, Docker Engine + Compose plugin ≥ 2.20 (`name:` top-level key,
  `--wait`), ufw 22/80/443, ssh key-only.
- **Contact email** (Q-4) for the `/terms` and `/privacy` pages (`web/src/components/Terms.svelte`, `Privacy.svelte` exist
  as placeholders) and for Caddy's ACME account (`email` in a global block — optional but recommended).
Exact deploy prerequisites on the VM: clone `github.com/BigSpoon33/pdf-splitter` (public; the build also needs
`github.com` for the engine tag, `docker.io`, `ghcr.io`, `pypi.org`), `deploy/.env` with `PDFSPLIT_IP_SALT=$(openssl rand
-hex 32)`, `PUBLIC_HOST`, `PDFSPLIT_PUBLIC_URL`; ports 80/443 published by caddy by default; the `jobs` volume
(`pdfsplit_jobs`) and Caddy's certificates (`pdfsplit_caddy_data`) live under `/var/lib/docker/volumes/` — never `down -v`
a live stack; uploads are auto-deleted after `PDFSPLIT_TTL_HOURS` so no backups are needed (story Out of Scope). `PUBLIC_HOST`
decides TLS: a public hostname → Let's Encrypt via TLS-ALPN/HTTP-01 on 443/80; `localhost` → internal CA. If the VM's
docker already uses `172.30.0.0/24` or `172.30.1.0/24` (or the ULA `fd30:5eaf:9a13::/48`), set the subnet AND the
addresses in it together (`PDFSPLIT_SUBNET`/`PDFSPLIT_SUBNET6`/`PDFSPLIT_CADDY_IP`/`PDFSPLIT_CADDY_IP6`,
`PDFSPLIT_EDGE_SUBNET`/`PDFSPLIT_EDGE_SUBNET6`). The api has no egress to the internet or the LAN (its network is
internal), so nothing in the api may ever need to call out — a future webhook or outbound check belongs in caddy's
network or a new service. **But the api CAN reach the VM itself** at the backend gateway (`172.30.0.1` and
`fd30:5eaf:9a13::1` by default): `internal` only stops forwarding, and sshd on `0.0.0.0`/`[::]` answers there (gate r2,
seen live with Ollama on the laptop). Add the host rule BEFORE the first public `up`, ahead of every allow, both
families — ufw (Ubuntu): in `/etc/ufw/before.rules`, in the `*filter` section's `ufw-before-input` chain right after
the `RELATED,ESTABLISHED` line, `-A ufw-before-input -s 172.30.0.0/24 -j DROP`; in `/etc/ufw/before6.rules` at the same
spot, `-A ufw-before-input -s fd30:5eaf:9a13::/64 -j DROP`; then `ufw reload`. nftables (Debian without ufw), in
`/etc/nftables.conf` under `chain input` before the accept rules: `ip saddr 172.30.0.0/24 drop` and `ip6 saddr
fd30:5eaf:9a13::/64 drop`, then `nft -f /etc/nftables.conf`. (Adjust the subnets if `deploy/.env` changed them.) Check,
from inside the running api, that every line says `blocked`:
`docker compose -p pdfsplit -f deploy/compose.yaml exec api python -c 'import socket
for host in ("172.30.0.1", "fd30:5eaf:9a13::1"):
    try: socket.create_connection((host, 22), timeout=2).close(); print("REACHED", host)
    except OSError as e: print("blocked", host, type(e).__name__)'` — or run `./deploy/smoke.sh` on the VM: its
host-reachability step prints a WARNING naming the reachable ports while the rule is missing (README § Deploy has the
same block). The `overloaded` and `too_slow` codes already have SPA messages (gates r1/r2); the one `web/` follow-up for
014 to fold in with the terms/privacy pages is the favicon CSP note above. The uptime check (AC-5 of 014)
should hit `https://<domain>/api/health` and expect `"ok":true` + `"engine_version":"0.4.2"`; ntfy is LAN-only, so a cloud
cron / healthchecks.io ping is the likely answer — decide in-story.

## Out-of-Scope Items
- Favicon `data:,` vs. the CSP (cosmetic; § CSP above) — `web/` is STORY-014's to touch.
- A genuinely non-LAN build (a VM or a CI runner without the Gitea LAN) was not available; AC-5 holds by construction.
- `pids_limit` / `ulimits` on the worker container, Caddy's own `cap_drop`, HSTS: hardening beyond the ACs; none needed
  for the ACs and each changes behaviour the VM story may want to decide (HSTS especially).
- `docker compose down --rmi` semantics (§ Bugs) are worth a line in `PROVISION.md`.
