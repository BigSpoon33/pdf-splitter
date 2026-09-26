# KICKOFF — STORY-013: Deploy: containers, compose, Caddy, local smoke test

## What you're walking into

"pdf-splitter" is a focused public tool: upload a PDF, split it by chapters or page ranges, download, auto-delete.
STORY-001..012 + 015/016 built the engine surface, the API, the sandboxed worker, the SPA, rate limits, the janitor
and the per-sheet cut geometry; everything works on this laptop as three processes (`pdf-splitter api`,
`pdf-splitter worker`, Vite). STORY-013 packages that as ONE `docker compose` stack — Caddy (TLS + static SPA +
`/api` proxy), api, worker — plus a smoke test that drives the whole thing end to end, so the STORY-014 VM deploy is
a copy of what was tested locally. Story file: `docs/stories/STORY-013.md` (AC-1..5 **and the orchestrator addendum
at its foot** — the proxy-header rule is part of this story).

- **Repo:** `~/Documents/Repos/pdf-splitter` (`origin` = GitHub `BigSpoon33/pdf-splitter`, `gitea` = LAN mirror),
  branch **`feature/mvp`**. Tip: `docs: STORY-016 - findings + KICKOFF-STORY-013` on top of `c410caa chore:
  STORY-016 - pin monograph-splitter v0.4.2` (the engine pin), `b6a8c3b docs: STORY-012 - gate passed; loop advances
  to STORY-016`, `cbb1a76 test: STORY-012 - an allocator failure inside the cut stays resources`, `802fdfc fix:
  STORY-012 - gate r2 …`, `76940a7 fix: STORY-012 - gate r1 …`, `7e5af5e feat: STORY-012 - rate limit, disk guard,
  24 h janitor and queue position`. Anything after the docs commit is the orchestrator's (`git log --oneline -8`).
  `docs/loop-state.json` belongs to the orchestrator: never stage it.
- **Baselines:** `uv run pytest -q` → **427 passed** (≈ 94 s; the real-subprocess tests dominate), `uv run ruff
  check` clean; `cd web && bun run check` → 0 errors 0 warnings (337 files), `bun run test` → **284 passed** (21
  files), `bun run build` → `dist/index.html` + `dist/assets/index-*.js` (109.15 kB, 38.89 kB gzip) + `index-*.css`.
  `web/dist/` and `web/.env.local` are gitignored; `jobs/`, `*.db*` too.
- **Engine:** pinned `monograph-splitter @ git+https://github.com/BigSpoon33/pdf-splitter-engine@v0.4.2`
  (`pyproject.toml:12`; `uv.lock` resolves it to `116a4bb7…`, public GitHub — no LAN, no Gitea). `pymupdf==1.28.2`
  pinned exactly. `requires-python >= 3.12`, `.python-version` = `3.12`; `uv 0.12.10`, Bun `1.3.5` on this laptop.
  Docker `29.7.2` + Compose `v5.5.1` are installed here and the daemon answers (`docker info`); no `caddy` binary on
  the host (it runs in its container). No `Dockerfile`, no `deploy/`, no `.dockerignore` exist yet.
- **Ports on this laptop:** 8000 is taken by something else — every live check so far ran the API on **8010** and
  Vite on **5181** (`API_PORT=8010 bun run dev --port 5181 --strictPort`). Caddy's published ports must not collide
  with those or with 8000; pick e.g. `8480`/`8443` locally via an env default, or publish only what the smoke test
  needs. Headless browser only (`~/Documents/AI/Chrono/skills/Browser/node_modules/playwright` + chromium; a
  `bun run shot.ts` with `chromium.launch({headless: true})` works — STORY-016's findings show a full upload → preview →
  split drive).
- Read, in order: `docs/stories/STORY-013.md` (all of it, addendum included); `docs/Architecture.md` § Component Map
  (`:25-53` — the `deploy/` tree it expects), § api / § worker (`:104-215` — what each process needs on disk), ADR-006
  (`:299`, SPA served by Caddy), ADR-008 (`:311`, the compose decision), § Dependency Map (`:347`);
  `docs/findings/STORY-012-findings.md` (§ Orchestrator decisions — the trusted-proxy rule, the salt, the janitor,
  the output budget; § Handoff); `docs/findings/STORY-016-findings.md` § Handoff; `README.md` § Configuration
  (`:71-89`, every `PDFSPLIT_*` variable) and § Web (`:91`).

## What exists today (reuse, don't re-invent)

- **The two processes are one console script:** `src/pdf_splitter/cli.py:15` `main` — `pdf-splitter api [--host
  127.0.0.1] [--port 8000]` runs `uvicorn.run(create_app(Settings()), host, port, access_log=False)` (`:28`) and
  `pdf-splitter worker` runs `Runner(Settings(), kinds=("analyze", "cut")).serve(stop)` with SIGTERM/SIGINT → stop
  claiming, finish running jobs (`:30-35`; a job cut short by the container's final kill is re-queued by the next
  start's sweep, `tests/test_worker.py::test_serve_runs_the_sweep_on_start`). `pyproject.toml:17` `[project.scripts]
  pdf-splitter = "pdf_splitter.cli:main"`. **The api's default host is 127.0.0.1** — in a container it must bind
  `0.0.0.0` (`--host 0.0.0.0`) or Caddy can't reach it.
- **Settings = environment only:** `src/pdf_splitter/config.py:12` `Settings` (`pydantic-settings`, `env_prefix
  PDFSPLIT_`): `jobs_dir` (always made absolute, `:40`; README says production `/jobs`), `max_bytes` 200 MiB,
  `max_pages`, `ttl_hours` 24, `workers` 2, `rate_per_hour` 6, `min_free_gb` 2, `trusted_proxy` (None),
  `ip_salt` (None), `max_output_bytes`, `analyze_timeout` 300, `cut_timeout` 600, `public_url`
  `http://localhost:8000`. Contracts: `tests/test_config.py::test_defaults`, `::test_env_prefix`,
  `::test_jobs_dir_is_always_absolute`, `::test_unprefixed_env_is_ignored`.
- **Trusted proxy (the addendum's subject):** `src/pdf_splitter/ratelimit.py:27` `client_ip(request, trusted_proxy)`
  — `X-Forwarded-For`'s LAST hop is used only when `request.client.host == trusted_proxy`; otherwise the peer.
  Contract: `tests/test_limits.py::test_client_ip_believes_x_forwarded_for_only_from_the_trusted_proxy`;
  `::test_ip_hash_is_salted_shared_by_secret_and_rotates_daily` (why `PDFSPLIT_IP_SALT` must be set when several
  api processes share `jobs.db`; ONE uvicorn process needs none). uvicorn's own `proxy_headers` defaults to **True**
  (`ProxyHeadersMiddleware`, trusting `forwarded_allow_ips` = 127.0.0.1 by default) — the addendum wants it OFF
  (`uvicorn.run(..., proxy_headers=False)`) so the app's rule is the only XFF logic; that is a one-line change in
  `cli.py:28` (in this story's authority) — add a test in `tests/test_limits.py` style or assert the config in the
  smoke test as the addendum says (spoofed XFF straight to the api port is counted under the real peer).
- **The sandbox (what `read_only`/`cap_drop`/`network_mode: none` must not break):** `src/pdf_splitter/worker/sandbox.py:38`
  `command(lim, python_args)` = `[sys.executable, "-m", "pdf_splitter.worker.sandbox", "--as", "--cpu", "--fsize",
  "--", …]` — the image's venv python re-execs itself under `RLIMIT_AS` 2 GB / `RLIMIT_CPU` / `RLIMIT_FSIZE` 1 GiB
  (`:19-35`); `runner.py:119` `_spawn` passes `PDFSPLIT_JOBS_DIR` and `PDFSPLIT_MAX_OUTPUT_BYTES` through the
  environment and `start_new_session=True`. Tasks write only under the job dir (`<jobs>/<id>/work`, `png/`,
  `result.zip`) and PyMuPDF/Python may use `/tmp` — so the worker needs the `jobs` volume writable, a tmpfs `/tmp`,
  and nothing else writable. `setrlimit` needs no capability. The **api also spawns sandboxed subprocesses**
  (`routes/preview.py:33` `run_preview` for sheet PNGs and section plans, `upload.py` for the preflight) — the api
  image is the same Python image, and it writes the upload + `png/` cache into the same volume. SQLite `jobs.db`
  (+ `-wal`/`-shm`) lives in `PDFSPLIT_JOBS_DIR` and is shared by api and worker on one host (story note).
- **The SPA is static + same-origin `/api`:** `web/src/lib/api.ts:71` builds `/api/jobs/...` URLs (no base URL
  anywhere), routes are `/`, `/j/<id>`, `/privacy`, `/terms` (`web/src/lib/route.ts:2`) — Caddy needs the SPA
  fallback (`try_files {path} /index.html`) for `/j/<id>` reloads. Uploads are a multipart `POST /api/jobs`
  (`api.ts:137`, XHR for progress); the 200 MiB upload cap is the api's `PDFSPLIT_MAX_BYTES`, Caddy's
  `request_body max_size 210MB` sits just above it so the api's own 413 wins the error message. Downloads are
  `GET /api/jobs/{id}/result.zip` and `/sections/{i}.pdf` (`Content-Disposition` attachment,
  `tests/test_api_e2e.py::test_end_to_end_upload_analyze_plan_cut_download`). Sheet PNGs are `img src=/api/...`
  — `img-src 'self'` covers them; `blob:` is for object URLs the SPA creates (story AC-3 CSP).
- **The e2e flow the smoke test must reproduce, with its contract:** `tests/test_api_e2e.py:73`
  `test_end_to_end_upload_analyze_plan_cut_download` — `POST /api/jobs` (multipart `file`) → 201 `{id, state}`;
  poll `GET /api/jobs/{id}` until `state == "review"` (`kind analyze`, `progress/total`); `GET /plan` → `PUT /plan`
  (same body, 200); `POST /cut` → 202 `{id, state: "queued"}`; poll to `done`; `GET /result.zip` → 200
  `application/zip` with `NNN-<slug>.pdf` × 3 + `manifest.json` for the synthetic book; `DELETE` → 204 then 410.
  The synthetic 2-column book is `tests/fixtures/books.py:29` `headed_book(path, outline=False)` (6 pages, 3
  level-1 headings → 3 sections); the smoke script can build it with `uv run python -c "from tests.fixtures.books
  import headed_book; headed_book(Path('…'))"` on the host (PyMuPDF is in the dev env) or bake a copy into
  `deploy/`. Rate limit is 6 uploads per client per sliding hour (`PDFSPLIT_RATE_PER_HOUR`) — a smoke run uploads
  once or twice, fine; a loop of smoke runs inside an hour hits 429 (`Retry-After`) unless the stack's env raises it.
- **Health:** `GET /api/health` → `{ok, queue, disk_free_gb, engine_version}`
  (`tests/test_health.py::test_health_shape_and_jobs_dir_created`) — the natural compose `healthcheck` for the api
  and the smoke test's readiness probe (`engine_version == "0.4.2"`).
- **Timeouts to respect in the proxy:** analyze up to 300 s, cut up to 600 s are WORKER-side; the api's own long
  request is a section-plan preview that may re-index inside a 20 s window (`routes/preview.py`), so Caddy's
  default upstream timeouts are fine. Uploads of 200 MiB need Caddy's body cap ≥ the api's, nothing else.

## Contracts (the tests ARE the contract — never hand-write sample JSON)

- API shapes and codes: `tests/test_api_e2e.py` (all), `src/pdf_splitter/errors.py:MESSAGES` (error codes), the
  e2e test above for the smoke sequence.
- Rate limit / proxy / salt: `tests/test_limits.py::test_client_ip_believes_x_forwarded_for_only_from_the_trusted_proxy`,
  `::test_ip_hash_is_salted_shared_by_secret_and_rotates_daily`, `::test_rate_window_slides_and_says_how_long_to_wait`.
- Settings from env: `tests/test_config.py` (all four).
- Worker lifecycle: `tests/test_worker.py::test_serve_runs_the_sweep_on_start`, `::test_the_sweep_skips_this_workers_own_jobs`;
  sandbox limits: `tests/test_cut.py::test_runner_fails_a_cut_whose_zip_hits_rlimit_fsize_as_too_large_output`,
  `::test_a_section_the_sandbox_refuses_is_too_large_output_on_both_paths` (these run the REAL launcher — they must
  still pass on the host after any change to `sandbox.py`/`runner.py`; nothing here should need one).
- Health: `tests/test_health.py::test_health_shape_and_jobs_dir_created`.

## Critical gotchas

1. **`uv sync` inside the image needs `git` and the public network** (the engine is a git dependency on GitHub):
   the Python stage needs `git` installed before `uv sync --frozen --no-dev` (or `--no-install-project` then copy
   `src/`), and `uv.lock` must be COPIED in unchanged — never re-lock inside the build. AC-5 is exactly "no LAN": do
   not reference `git.gumshu.duckdns.org` anywhere in the build. Test it with `docker build --no-cache` (the laptop
   can reach GitHub; the LAN-free claim is by construction — say so in the findings if no non-LAN host is available).
2. **Two images, one Python base:** api and worker run the same code (`pdf-splitter api` / `pdf-splitter worker`);
   one image, two services with different `command:`. The worker's `network_mode: none` is fine because it fetches
   nothing at runtime; the api's network is only the compose network (Caddy → api). `read_only: true` + `tmpfs:
   /tmp` + the `jobs` volume mounted at `/jobs` (`PDFSPLIT_JOBS_DIR=/jobs`) — `Settings` makes it absolute anyway.
   A non-root user must own `/jobs` (name the uid in the Dockerfile and `user:` in compose, or `chown` in an
   entrypoint) or the api's `mkdir` on startup fails.
3. **uvicorn must bind `0.0.0.0` in the api container** (`--host 0.0.0.0`; default is 127.0.0.1) and Caddy proxies
   to `api:8000`. Do NOT publish 8000 on the host (story AC-2: api internal only); the smoke test's "spoofed XFF
   straight to the api port" check (addendum) needs a way in: either `docker compose exec caddy` / a one-off
   container on the compose network with `curl`, or a temporary port publish under the throwaway project — keep the
   production compose file unpublished and use an override file for the smoke run.
4. **Proxy headers:** set `PDFSPLIT_TRUSTED_PROXY` to Caddy's FIXED address on the compose network (define a
   subnet + `ipv4_address` for caddy; a service name is not what `request.client.host` sees). `uvicorn.run(...,
   proxy_headers=False)` in `cli.py:28`. `PDFSPLIT_IP_SALT` from `.env`/env (not in git; the smoke test may set a
   throwaway). Then a request with `X-Forwarded-For: 1.2.3.4` sent directly to the api (bypassing Caddy) must be
   counted under the real peer — the addendum's test.
5. **Caddy specifics (AC-3):** `{$PUBLIC_HOST}` as the site address with a local default of `localhost` (Caddy
   serves `localhost` with its internal CA — `curl -k` / `--cacert` in the smoke test, or `http://` on a plain port
   for local runs via `auto_https off`/an `http://` address; decide and document), `handle /api/*` →
   `reverse_proxy api:8000`, `request_body { max_size 210MB }`, `encode zstd gzip`, `header` with
   `Content-Security-Policy "default-src 'self'; img-src 'self' blob:"` (check the SPA still runs under it — Vite's
   production build inlines nothing external; today's `dist/index.html` has one external `<script type=module src=/assets/…>` and no inline
   script/style, so `default-src 'self'` holds; if a future build inlines anything the policy needs a hash — report,
   don't widen silently), `X-Content-Type-Options
   nosniff`, `Referrer-Policy`, and `try_files {path} /index.html` for the SPA (`/j/<id>` deep links).
6. **Compose project isolation for the smoke test (AC-4):** `docker compose -p pdfsplit-smoke-$$ -f deploy/compose.yaml
   [-f deploy/compose.smoke.yaml] up -d --build --wait`, distinct volume/network names come free with `-p`; ALWAYS
   `down -v` in a `trap` so a failed run leaves nothing. Never `docker system prune`. The Docker daemon is shared
   with other things on this laptop (`docker ps` first; don't stop containers you didn't start).
7. **Process safety on the host:** stop dev servers by explicit PID (`lsof -ti :8010`, `ps -eo pid,args | grep
   "[v]env/bin/pdf-splitter"`), never `pkill -f`/`killall` (a pattern from your own command line kills you).
8. **Bun/uv only; commit messages end with the Co-Authored-By line; stage explicit paths (never `git add -A`/`.`;
   never `docs/loop-state.json`); never `reset --hard` / `checkout .`.** Keep `web/dist/` ignored: the image builds it.
9. **Secrets:** nothing in this story needs a credential. `.env` for compose is gitignored (add it to `.gitignore`
   if you create one) with a committed `.env.example`.

## Recommended AC ordering

1. **AC-1 Dockerfile** (repo root): stage `web` = `oven/bun` → `bun install --frozen-lockfile` + `bun run build`
   (`web/package.json:6-12`; note `web/bun.lock` is the app's own lockfile, standalone); stage `python` =
   `python:3.12-slim` + uv (copy from `ghcr.io/astral-sh/uv`) + `git`, `uv sync --frozen --no-dev` against the
   copied `pyproject.toml`/`uv.lock`, copy `src/`, non-root user, `ENTRYPOINT ["pdf-splitter"]` (via the venv);
   stage `caddy` = `caddy:2` + `COPY --from=web /web/dist /srv`. `.dockerignore`: `.venv`, `web/node_modules`,
   `web/dist`, `jobs`, `docs`, `.git`. Verify each stage builds (`docker build --target …`).
2. **AC-2 `deploy/compose.yaml`:** services `caddy` (ports 80/443 → env-overridable, `Caddyfile` mounted or
   copied, `PUBLIC_HOST`), `api` (`command: api --host 0.0.0.0 --port 8000`, `expose: 8000`, env
   `PDFSPLIT_JOBS_DIR=/jobs`, `PDFSPLIT_TRUSTED_PROXY=<caddy ip>`, `PDFSPLIT_IP_SALT=${PDFSPLIT_IP_SALT}`,
   `PDFSPLIT_PUBLIC_URL`, healthcheck on `/api/health`), `worker` (`command: worker`, `network_mode: none`,
   `read_only: true`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, `tmpfs: [/tmp]`, `mem_limit`,
   same env), volume `jobs` on both, a network with a fixed subnet so caddy gets `ipv4_address`. `cli.py:28`
   `proxy_headers=False` (+ a test).
3. **AC-3 `deploy/Caddyfile`** as in gotcha 5; run `caddy validate` inside the image (`docker run --rm -v …
   caddy:2 caddy validate --config /etc/caddy/Caddyfile`).
4. **AC-4 `deploy/smoke.sh`:** builds the synthetic book, `up --build --wait` under a throwaway project,
   waits for `/api/health` `ok` through Caddy, uploads (`curl -F file=@book.pdf`), polls to `review`, `GET
   /plan` → `PUT /plan`, `POST /cut`, polls to `done`, downloads `result.zip`, asserts 3 PDFs
   (`unzip -l` / `python -m zipfile -l`), then the addendum's XFF check (two uploads with different spoofed XFF
   straight to the api → both counted under the same peer, visible as the 7th upload being 429 — or read the
   `rate` rows' hash equality through `sqlite3` on the volume — pick the cheaper assertion and cite it), `DELETE`,
   `down -v`. `set -euo pipefail`, a `trap` for teardown, exit 0/1. Run it for real; put its output in the findings.
5. **AC-5:** the build fetches the engine from GitHub only (grep the Dockerfile/compose/Caddyfile for
   `gumshu`/`192.168.` → none); `docker build --no-cache` succeeds on this laptop; state in the findings whether a
   genuinely non-LAN build ran (CI on egg0's runner is LAN — it does not count) or the claim is by construction.
6. Docs: `README.md` gets a **Deploy** section (compose up, env vars, smoke test); `docs/Architecture.md` ADR-008 gets
   an "as built" line (the fixed caddy address / trusted proxy, ports), § Component Map's `deploy/` tree corrected to
   what exists; `.env.example`.

## Conventions

- Shell scripts: `bash`, `set -euo pipefail`, functions over copy-paste, a `trap` for teardown. Python: FastAPI style
  as in `src/`. Comments say WHY, never WHAT. No new docs beyond README § Deploy, the ADR-008 "as built" line and
  `.env.example`.
- Commit `feat: STORY-013 - containers, compose, Caddy and an end-to-end smoke test` on `feature/mvp`, ending with
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`; stage explicit paths only. If `cli.py`'s
  `proxy_headers=False` lands, it goes in the same commit (it is the addendum). Push `git push origin feature/mvp &&
  git push gitea feature/mvp`.
- Findings + next kickoff: `docs/findings/STORY-013-findings.md`, `docs/KICKOFF-STORY-014.md` (STORY-014 is HELD in
  the loop — write the kickoff anyway, the orchestrator decides when it runs), committed as `docs: STORY-013 -
  findings + KICKOFF-STORY-014`; set STORY-013's status lines to Done.

## Authority

- Free: `Dockerfile`, `.dockerignore`, `deploy/{compose.yaml,compose.smoke.yaml,Caddyfile,smoke.sh}`, `.env.example`,
  `.gitignore` (add `.env`), `README.md` § Deploy, `docs/Architecture.md` (ADR-008 as-built line + the `deploy/` tree),
  `src/pdf_splitter/cli.py` (the `proxy_headers=False` line and, if needed, a `--host` default — keep the CLI's
  existing flags), one test for it under `tests/`, `docs/findings/`, `KICKOFF-*`, STORY-013's status lines.
- Do not touch: the engine repo, `pyproject.toml` deps / `uv.lock` (no new runtime deps; if the build genuinely
  needs one, stop and report), `web/` source, the API/worker code beyond `cli.py`, `docs/loop-state.json`, the PRD,
  `main`.
- Out of scope: the public VM, DNS, real certificates, uptime ntfy (STORY-014); terms/privacy text (STORY-014);
  a deploy.sh over ssh (ADR-008 mentions it — STORY-014's).

## Stopping conditions (BLOCKED protocol)

- Docker cannot build or run here (daemon down, permission denied) — report, don't sudo around it.
- The image cannot install the engine from GitHub (network, tag missing) — check `git ls-remote --tags
  https://github.com/BigSpoon33/pdf-splitter-engine` shows `v0.4.2` first; if it does and the build still fails,
  report.
- A pre-existing test fails for reasons unrelated to your change.
- The story's CSP breaks the built SPA (inline script/style in `dist/index.html`) — report the exact violation and
  the policy that would be needed instead of widening it.
- The addendum's XFF assertion cannot be made observable without new API surface — report what would be needed.

## Final report shape

Per-AC ✅/❌ with file:line; `docker build` result per stage (image sizes); the compose services' final config
(ports, the caddy fixed address, env); `smoke.sh` transcript summary (each step's status code, the 3 PDF names,
the XFF check's evidence, teardown clean — `docker volume ls`/`docker ps -a` show nothing of the throwaway project);
counts (`uv run pytest -q` 427 → N, `bun run test` 284 → N, `bun run check`, `bun run build`); `caddy validate`;
what was verified about "no LAN" for AC-5; the commit sha on both remotes; decisions taken; what STORY-014 should
know (which env vars the VM needs, what `PUBLIC_HOST` does to TLS, where the volume lives).

## Orchestrator addendum (binding)

- This laptop runs OTHER docker/compose stacks (e.g. an Inkwell docs stack). Never use the default
  project name, default network, or host ports 80/443/8000: run everything under a unique compose
  project (`-p pdfsplit-smoke`), a dedicated network/subnet, and high host ports (e.g. 18080/18443);
  never `docker compose down` / `docker rm` / `docker network prune` anything you didn't create; clean up
  only your own project (`docker compose -p pdfsplit-smoke down -v`). Check `docker ps` before and after.
- Images/build cache live under /var/lib/docker (not /tmp); still prune only your own dangling images.
- The STORY-013 story addendum (uvicorn proxy_headers=False, PDFSPLIT_TRUSTED_PROXY = Caddy's fixed
  compose IP, PDFSPLIT_IP_SALT from an env secret, smoke test for spoofed XFF) is binding.

## Previous attempt (RETRY — read this first)

Attempt 1 (`cce9bd1`, findings `7e34206`) failed with 3 CONFIRMED findings + 1 orchestrator hardening —
`docs/findings/STORY-013-review.md`. Fix forward, one commit on the feature/mvp tip:
`fix: STORY-013 - gate r1: uploads guarded before the body is read, real IPv6 client addresses, api without egress, smoke on an empty host`
1. **Uploads can't exhaust the api**:
   - A tiny ASGI middleware for `POST /api/jobs` that runs BEFORE the body is parsed: reject with 413
     `too_large` when `Content-Length` > MAX_BYTES + multipart slack (or is absent/chunked → 411/413),
     and take the rate slot there (move `take_slot` out of `_accept`; the route must not take a second
     slot; keep 429 + Retry-After semantics). Keep the streaming cap in `_copy_capped` as a backstop.
   - Spool on disk, not RAM: set `TMPDIR` for the api to a directory on the jobs volume (e.g.
     `/jobs/.spool`, created by the app at start, excluded from the janitor's orphan reaping and
     cleaned of stale files older than 1 h), and size the remaining /tmp tmpfs (e.g. 64m).
   - Cap concurrency: uvicorn `limit_concurrency` (config, default e.g. 64) and a small
     in-flight-uploads cap (e.g. 4 concurrent uploads → 503 `busy`/`disk_full`-style code with Retry-After).
   Tests (pytest): oversized Content-Length refused before any spool file exists; rate slot taken
   before the body is read; concurrent-upload cap. Live (isolated project, swapless api like the
   review): 8 × 300 MiB concurrent → no OOM, api stays up, requests get 413/429/503 as appropriate.
2. **Real IPv6 addresses**: enable IPv6 on the compose network (`enable_ipv6: true`, a ULA /64 subnet,
   Caddy fixed at a v6 address too) so Docker DNATs v6 natively; `PDFSPLIT_TRUSTED_PROXY` accepts a
   comma-separated list (Caddy's v4 AND v6 address). If the host's Docker can't do ip6tables, document
   the fallback (no AAAA record) in the handoff. Live: `curl -6` through Caddy → the api sees the real
   v6 client (a distinct rate bucket from IPv4 clients). Fix the handoff text accordingly.
3. **api without egress (orchestrator hardening)**: two networks — `edge` (caddy only, published
   ports, outbound for ACME) and `backend` (`internal: true`; caddy + api). Worker stays
   `network_mode: none`. Live: from inside the api, github.com and the LAN are unreachable; Caddy→api
   still works; ACME-capable caddy still has egress.
4. **smoke.sh** `others()` tolerant of zero containers (`|| true`); verify with a stubbed docker.
ISOLATION rules from the addendum still apply (unique project, subnets 172.27.x / a ULA, high ports,
touch nothing else, `docker ps` before/after identical). Update findings ("Gate r1 fixes"),
Architecture ADR-005/ADR-008 as-built, README § Deploy, and the STORY-014 handoff.

## Attempt 2b — regression from 6ef0253 (standing auto-fix policy) — READ THIS FIRST

6ef0253 added the error code `overloaded` to `src/pdf_splitter/errors.py` without a message in
`web/src/lib/errors.ts`; the SPA parity test (errors.test.ts reads errors.py) now fails: 2 failed / 283.
One commit: `fix: STORY-013 - the SPA knows the overloaded code`
- Add `overloaded` to `web/src/lib/errors.ts` (message like "The service is busy right now — try again in
  a few seconds.") and to the `JobErrorCode`/api types if they enumerate codes; honour `Retry-After`
  nowhere new (just the message). DropZone shows it in place like other upload errors.
- `cd web && bun run check && bun run test && bun run build` all green (expect 285+ passing, 0 warnings);
  `uv run pytest -q` still green. Note it in findings ("Gate r1 fixes" addendum). Push to origin + gitea.

## Attempt 2c — round-2 fixes (standing auto-fix policy) — READ THIS FIRST

5 confirmed findings — `docs/findings/STORY-013-review.md` § Round 2. One commit on the feature/mvp tip:
`fix: STORY-013 - gate r2: slow bodies can't pin the caps, v6 keyed per /64, host reachability documented and probed`
1. **Slow bodies** (no hard wall-clock cap — genuine slow uploads must still work):
   - Uploads: wrap `receive` in UploadGuard with a progress watchdog — abort (408 `too_slow`, new code
     + SPA message) when fewer than MIN_UPLOAD_RATE bytes (config, default 32 KiB) arrive in any
     30 s window, and a per-client in-flight cap (config, default 2 of the 4 slots, keyed by the same
     client hash) → 429 `rate_limited`/503 `overloaded` as fits.
   - Every other request with a body (e.g. PUT /plan): a small pure-ASGI BodyGuard: Content-Length
     required ≤ MAX_JSON_BYTES (config, default 4 MiB; 413 otherwise), and the body must arrive within
     a short bound (config, default 20 s → 408). Chunked bodies refused (411) as for uploads.
   - Tests prove each with a trickling ASGI receive (no real sleeps: inject the clock). Live (isolated
     project): 4 trickled uploads from one client no longer lock out a second client; 63 trickled PUTs
     don't take the api unhealthy.
2. **v6 per /64**: the rate key for IPv6 clients is the /64 prefix (IPv4 unchanged; v4-mapped v6 →
   the v4 address). Replace the /128-distinctness test with /64 grouping + distinct /64s distinct.
3. **Host reachability**: correct README/Architecture (the api can't reach the internet/LAN, but CAN
   reach host services via its network's gateway unless the host firewall drops it); the smoke probes the
   BACKEND gateway (v4+v6) and prints the result as a WARNING (it can't fix the host); the STORY-014
   handoff gets the exact host firewall rule to add on the VM (nftables/ufw: drop INPUT from the
   backend subnets, v4+v6, both before-rules) and a check to run after.
4. **Real "before the body" tests**: instrument `receive` (count body messages/bytes consumed) instead
   of listing the spool dir; prove they fail if the guard reads the body first.
5. **Docs**: fix the handoff (`overloaded` done) and § API Interface (411/415/408/503 overloaded; which
   refusals spend a slot).
All suites green (pytest, ruff, web check/test/build); ISOLATION rules unchanged (own project, subnets
NOT 172.26.x — multica_default holds 172.26.0.0/16 — own ULAs, high ports, `docker ps` identical).

## Attempt 3 — SIMPLIFY (Shuma's decision, 2026-09-26) — READ THIS FIRST

Round 3 (`docs/findings/STORY-013-review.md` § Round 3) showed the in-app slow-body watchdog loses data.
Shuma chose to simplify. One commit on the feature/mvp tip:
`fix: STORY-013 - gate r3: slow bodies bounded at the proxy, no in-app watchdog; per-client body caps; correct host firewall rules`
1. **Remove the in-app upload watchdog** (Progress/wait_for around receive, `too_slow` 408 from the
   guard, MIN_UPLOAD_RATE config). The upload path must never cancel a `receive()`. Keep: Content-Length
   pre-check, rate slot before the body, spool on the jobs volume, server cap (4) and per-client cap (2).
   Keep the `too_slow` error code only if something still emits it; otherwise remove it from errors.py
   AND errors.ts (keep the parity test green).
2. **Bound bodies at Caddy**: global `servers { timeouts { read_header 15s  read_body 30m  idle 2m } }`
   (read_body generous enough for 200 MiB at ~115 KiB/s); document the numbers and the trade-off.
   Verify live that a steady upload at ~150 KiB/s of 64 MiB completes, and that a stalled body is cut at
   the configured bound (use a short override in the smoke, e.g. 20 s, so the smoke stays fast).
3. **Per-client cap across midnight**: key the in-flight caps with the same day-spanning identity the
   rate window uses (e.g. the canonical `rate_key` salted independently of the date, or count both
   window hashes) so 00:00 UTC never grants a fresh cap. Test with a frozen clock.
4. **BodyGuard**: keep 4 MiB + 20 s; add a per-client concurrent-body cap (config, default 8) → 429 with
   Retry-After, unread; on a mid-body disconnect send nothing *and* make the access_log path not log a 500
   (e.g. return a 499-style silent close the access log treats as client-closed, or restructure so
   call_next isn't left without a response). Tests: re-opened held PUTs from one client can't fill
   limit_concurrency (other clients' /api/health stays 200); disconnect → no ERROR/traceback in logs.
5. **Correct host firewall docs** (README § Deploy + findings handoff): ufw v4 in before.rules
   `-A ufw-before-input …`, v6 in before6.rules `-A ufw6-before-input …`; nftables as a SEPARATE file
   (e.g. /etc/nftables.d/pdfsplit.nft with its own `table inet pdfsplit` + input chain hook priority
   filter - 1) loaded by `nft -f` of THAT file — never reload /etc/nftables.conf (its `flush ruleset`
   wipes Docker's tables). Validate both in throwaway `unshare -rn` namespaces with the stock files.
6. **Document the residual risk** (README/Architecture ADR-005/ADR-008 + findings): several /64s can
   hold the 4 upload slots for up to the Caddy read_body bound; follow-up story STORY-017 (per-IP
   connection limiting: CrowdSec/fail2ban or a Caddy rate-limit module).
All suites green (pytest, ruff, web check/test/build). ISOLATION rules unchanged.
