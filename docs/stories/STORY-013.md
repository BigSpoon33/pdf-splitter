# STORY-013: Deploy: containers, compose, Caddy, local smoke test

> **Status:** Done (2026-09-26)
> **Size:** M
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#ADR-008`
> **Repo:** `~/Documents/Repos/pdf-splitter`

---

## Summary

As the operator, I need the whole stack to run with one compose command, so that the VM deploy is a copy of what I tested locally.

---

## Context

Packaging before going public.

---

## Depends On

- STORY-011, STORY-012

---

## Acceptance Criteria

- [ ] AC-1: One Dockerfile, multi-stage: Bun builds `web/dist`; Python stage (uv, non-root user) is the api+worker image; Caddy image gets `web/dist`
- [ ] AC-2: `deploy/compose.yaml`: caddy (80/443, Caddyfile), api (internal only), worker (`network_mode: none`, `read_only: true`, `cap_drop: [ALL]`, tmpfs /tmp, `mem_limit`), shared volume `jobs`
- [ ] AC-3: Caddyfile: SPA fallback to index.html, `/api/*` → api:8000, `request_body max_size 210MB`, security headers (CSP default-src 'self'; img-src 'self' blob:), gzip/zstd; `{$PUBLIC_HOST}` site address (localhost locally)
- [ ] AC-4: `deploy/smoke.sh` brings the stack up under a throwaway project name, uploads the synthetic 2-column book, polls to review, PUTs a plan, cuts, downloads the zip, asserts 3 PDFs, tears down
- [ ] AC-5: The image builds on a machine with no LAN access (engine installs from public GitHub; verify with `docker build --network` default on a non-LAN host or CI)

---

## Files Affected

| File | Change Type | Notes |
|------|-------------|-------|
| `Dockerfile` | Create |  |
| `deploy/{compose.yaml,Caddyfile,smoke.sh}` | Create |  |
| `README.md` | Modify | deploy section |

---

## Implementation Notes

- Worker has no network, so it can't reach anything — good; the api reaches nothing either except Caddy.
- SQLite on a named volume shared by two containers is fine (same host); keep WAL files on the same volume.

---

## Out of Scope

- The public VM (STORY-014).

---

## Verification Steps

```bash
./deploy/smoke.sh
```

---

## E2E Test Plan

| Test | Type | What It Verifies |
|------|------|------------------|
| `deploy/smoke.sh` | New | AC-1..4 |

---

## Code Review Checklist

- [ ] No regressions or unintended side effects
- [ ] No security issues (injection, credential leaks, unsafe input)
- [ ] No changes outside this story's scope
- [ ] No dead code, debug artifacts, or leftover TODOs
- [ ] Style consistent with surrounding code

---

## Commit Message

```
feat: STORY-013 - containers, compose, Caddy and an end-to-end smoke test
```

---

## Status

**Done** (2026-09-26, `cce9bd1` + gate r1 fix on `feature/mvp`; findings in `docs/findings/STORY-013-findings.md`,
review in `docs/findings/STORY-013-review.md`)

- [x] AC-1
- [x] AC-2
- [x] AC-3
- [x] AC-4
- [x] AC-5 (by construction on this laptop; the VM build is the non-LAN proof)

> **Orchestrator addendum (from STORY-012 review):** uvicorn must not trust X-Forwarded-For on its own: run it with `proxy_headers=False` (the app's `PDFSPLIT_TRUSTED_PROXY` rule is the only XFF logic) and set `PDFSPLIT_TRUSTED_PROXY` to Caddy's container address in compose (a fixed IP on the compose network). Set `PDFSPLIT_IP_SALT` from an env secret (not in git). Test in smoke.sh: an upload with a spoofed XFF straight to the api port (bypassing Caddy) is counted under the real peer.
