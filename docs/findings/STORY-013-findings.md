# Findings — STORY-013
**Date:** 2026-09-26
**Status:** done

Commit `cce9bd1 feat: STORY-013 - containers, compose, Caddy and an end-to-end smoke test` on `feature/mvp`, pushed to
`origin` (GitHub) and `gitea`. Last story of the autonomous run; STORY-014 is held for Shuma (§ Handoff for STORY-014).
**Gate r1** (`docs/findings/STORY-013-review.md`) failed it with 3 confirmed findings + 1 hardening; the fix is the
commit `fix: STORY-013 - gate r1: uploads guarded before the body is read, real IPv6 client addresses, api without
egress, smoke on an empty host` on top of `7d9311a` — § Gate r1 fixes below. The file:line references in § AC
Verification are as of `cce9bd1`; § Gate r1 fixes cites the current ones.

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
`PDFSPLIT_EDGE_SUBNET`/`PDFSPLIT_EDGE_SUBNET6`). The api has no egress (its network is internal), so nothing in the api
may ever need to call out — a future webhook or outbound check belongs in caddy's network or a new service. Two `web/`
follow-ups for 014 to fold in with the terms/privacy pages: the `overloaded` code (503 + `Retry-After`, gate r1) needs a
line in the SPA's message map (`web/src/lib/errors.ts`; until then visitors see the fallback text), and the favicon CSP
note above. The uptime check (AC-5 of 014)
should hit `https://<domain>/api/health` and expect `"ok":true` + `"engine_version":"0.4.2"`; ntfy is LAN-only, so a cloud
cron / healthchecks.io ping is the likely answer — decide in-story. Favicon CSP note above is a one-line `web/` change for
014 to fold in with the terms/privacy pages.

## Out-of-Scope Items
- Favicon `data:,` vs. the CSP (cosmetic; § CSP above) — `web/` is STORY-014's to touch.
- A genuinely non-LAN build (a VM or a CI runner without the Gitea LAN) was not available; AC-5 holds by construction.
- `pids_limit` / `ulimits` on the worker container, Caddy's own `cap_drop`, HSTS: hardening beyond the ACs; none needed
  for the ACs and each changes behaviour the VM story may want to decide (HSTS especially).
- `docker compose down --rmi` semantics (§ Bugs) are worth a line in `PROVISION.md`.
