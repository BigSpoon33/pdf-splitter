# Review — STORY-013
**Date:** 2026-09-26
**Reviewed:** pdf-splitter cce9bd1 (feature/mvp)

## Round 1 — FAILED (3 confirmed, 1 refuted → orchestrator hardening)
Coverage highlights: non-root images, no secrets baked; worker truly network-less/read-only/cap-less/3 GiB; api not published; Caddy SPA fallback never swallows /api; body cap 413; CSP OK with the SPA; XFF through Caddy correct; smoke passes under an isolated project; other stacks untouched.

1. [correctness] compose.yaml:26-28 (+ :65, :75) — the api's /tmp is unsized tmpfs; Starlette spools every multipart file part to /tmp BEFORE the route runs (so before the rate slot and size cap); concurrent big uploads OOM-kill the api (8 × 300 MiB → OOMKilled, exit 137, all in-flight requests dropped); no concurrency cap. CONFIRMED live.
2. [correctness] findings handoff (AAAA) + compose.yaml:43-45,102-107 — IPv4-only network behind docker-proxy on `[::]`: every IPv6 visitor arrives from the bridge gateway → one shared rate bucket for all IPv6 visitors. CONFIRMED live.
3. [ac-gap] api egress — REFUTED as a story defect (ADR-005 accepted api-side preflight; AC-2 "internal only" = not published). Orchestrator decision: harden anyway (see retry).
4. [correctness] smoke.sh:38,59 — `grep -v` exits 1 on a host with no other containers → smoke aborts under set -euo pipefail. CONFIRMED.

## Round 2 (re-review of 6ef0253 + 3396e1a) — FAILED (5 confirmed; regressions/incomplete fixes) → auto-fix per standing policy
Verified: 8 × 300 MiB flood → 413, no OOM; spool on the volume; overloaded 503 in the SPA; v6 client addresses end to end; api blocked from internet/LAN; smoke on an empty host; isolation clean.
1. [correctness] upload.py:91-106 (+ cli.py:38, config.py:33-35) — the new caps are held while a body trickles in, with no time/rate bound anywhere (Caddy, uvicorn, app): 4 slow uploads pin all upload slots (everyone else 503 overloaded); ~63 slow PUT /plan bodies (no guard, job need not exist) fill limit_concurrency → the whole api 503s/unhealthy. CONFIRMED live.
2. [contract-divergence] ratelimit.py:43-51 — v6 keyed on the full /128 → one /64 = unlimited windows. CONFIRMED.
3. [correctness] compose.yaml:129-135 + README.md:142-143, Architecture.md:308-310, smoke.sh:124-133 — `internal: true` still lets the api reach the HOST via its network's gateway (host services on 0.0.0.0/[::] — Ollama 200 here; sshd on the VM); smoke only probes the other network's gateway. CONFIRMED live.
4. [test-gap] test_upload.py:426,468 — `spool_dir.iterdir()` can't see Starlette's unnamed O_TMPFILE; "refused before the body is read" tests pass even if the body was spooled. Reviewer-proven.
5. [ac-gap] findings :291-293/:114-115 and Architecture.md:223-224 — handoff says `overloaded` still needs an SPA message (added in 3396e1a); API Interface misses 411/415/503 overloaded and still says every refused upload spends a slot. Orchestrator-verified.
