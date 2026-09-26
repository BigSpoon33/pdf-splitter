# Review — STORY-013
**Date:** 2026-09-26
**Reviewed:** pdf-splitter cce9bd1 (feature/mvp)

## Round 1 — FAILED (3 confirmed, 1 refuted → orchestrator hardening)
Coverage highlights: non-root images, no secrets baked; worker truly network-less/read-only/cap-less/3 GiB; api not published; Caddy SPA fallback never swallows /api; body cap 413; CSP OK with the SPA; XFF through Caddy correct; smoke passes under an isolated project; other stacks untouched.

1. [correctness] compose.yaml:26-28 (+ :65, :75) — the api's /tmp is unsized tmpfs; Starlette spools every multipart file part to /tmp BEFORE the route runs (so before the rate slot and size cap); concurrent big uploads OOM-kill the api (8 × 300 MiB → OOMKilled, exit 137, all in-flight requests dropped); no concurrency cap. CONFIRMED live.
2. [correctness] findings handoff (AAAA) + compose.yaml:43-45,102-107 — IPv4-only network behind docker-proxy on `[::]`: every IPv6 visitor arrives from the bridge gateway → one shared rate bucket for all IPv6 visitors. CONFIRMED live.
3. [ac-gap] api egress — REFUTED as a story defect (ADR-005 accepted api-side preflight; AC-2 "internal only" = not published). Orchestrator decision: harden anyway (see retry).
4. [correctness] smoke.sh:38,59 — `grep -v` exits 1 on a host with no other containers → smoke aborts under set -euo pipefail. CONFIRMED.
