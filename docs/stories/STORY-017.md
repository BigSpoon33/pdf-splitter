# STORY-017: Per-IP connection limiting at the edge (follow-up, not scheduled)

> **Status:** Backlog (from STORY-013 round 3, Shuma's "simplify" decision, 2026-09-26)
> **Size:** S

## Summary
Clients using several IPv6 /64s (or many IPv4 addresses) can still hold the 4 upload slots for up to Caddy's read_body bound. Add per-IP connection/request limiting at the edge: CrowdSec or fail2ban on Caddy's access log, or a Caddy rate-limit module (xcaddy build), with per-/64 grouping for IPv6.

## Acceptance Criteria
- [ ] AC-1: N concurrent slow uploads from distinct /64s beyond a configured threshold are refused at the edge; genuine visitors unaffected.
- [ ] AC-2: documented and covered by the smoke test.
