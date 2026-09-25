# STORY-013: Deploy: containers, compose, Caddy, local smoke test

> **Status:** Pending
> **Size:** M
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#ADR-008`
> **Repo:** `~/Documents/Repos/monograph-splitter-web`

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
- [ ] AC-5: The engine dependency installs without LAN access (decision from STORY-004 implemented: public mirror or vendored wheel)

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

**Pending**

- [ ] AC-1
- [ ] AC-2
- [ ] AC-3
- [ ] AC-4
- [ ] AC-5
