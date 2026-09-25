# STORY-014: Deploy: public VM, domain, terms page, uptime alert

> **Status:** Pending
> **Size:** M
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#ADR-008`
> **Repo:** `~/Documents/Repos/monograph-splitter-web`

---

## Summary

As the operator, I need the site live on a public HTTPS domain with terms and monitoring, so that anyone can use it and I hear when it breaks.

---

## Context

Go-live. Needs Shuma's decisions Q-1 (name/domain), Q-2 (host account/region), Q-4 (contact email).

---

## Depends On

- STORY-013
- Shuma: domain registered + DNS access; Hetzner/DO account; contact email

---

## Acceptance Criteria

- [ ] AC-1: VM provisioned (2–4 vCPU, 4–8 GB, Ubuntu LTS/Debian), Docker installed, ufw 22/80/443 only, ssh key-only, unattended-upgrades on; steps captured in `deploy/PROVISION.md`
- [ ] AC-2: `deploy/deploy.sh` (ssh + git pull/rsync + `docker compose up -d --build`) redeploys in one command
- [ ] AC-3: `https://<domain>/` returns 200 with a valid Let's Encrypt cert; AC-1..AC-13 of the PRD pass on the live site (checklist in the story status)
- [ ] AC-4: /privacy and /terms pages: what's stored, 24 h deletion, no sharing, acceptable use (only files you have rights to), takedown contact
- [ ] AC-5: An uptime check hits `/api/health` every 5 min and posts to ntfy topic on failure (from the VM itself via cron + curl to the public ntfy? — ntfy is LAN-only today: use a cloud cron (e.g. healthchecks.io) or expose a separate ntfy path; decide in-story and record it)

---

## Files Affected

| File | Change Type | Notes |
|------|-------------|-------|
| `deploy/{PROVISION.md,deploy.sh}` | Create |  |
| `web/src/pages/{Privacy,Terms}.svelte` | Create |  |
| `docs/Architecture.md` | Modify | record hosting + monitoring decisions |

---

## Implementation Notes

- Creating cloud resources and registering a domain cost money and are outward-facing — confirm with Shuma at each step.

---

## Out of Scope

- CDN, backups (no persistent user data by design).

---

## Verification Steps

```bash
curl -sI https://<domain>/ | head -1
curl -s https://<domain>/api/health
```

---

## E2E Test Plan

| Test | Type | What It Verifies |
|------|------|------------------|
| `deploy/smoke.sh against the public URL` | Existing | AC-3 |

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
feat: STORY-014 - public deployment, terms/privacy pages and uptime alerting
```

---

## Status

**Pending**

- [ ] AC-1
- [ ] AC-2
- [ ] AC-3
- [ ] AC-4
- [ ] AC-5
