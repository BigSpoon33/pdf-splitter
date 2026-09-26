# Review — STORY-012
**Date:** 2026-09-26
**Reviewed:** pdf-splitter 7e5af5e (feature/mvp)

## Round 1 — FAILED (4 confirmed)
Coverage highlights: XFF only from the trusted proxy; raw IPs never stored/logged; janitor safe on live jobs, idempotent, 108-job sweep clean; queue_position correct; failed-cut recovery and terminal failed-analyze; addendum-2 read races → 410.

1. [correctness] upload.py:121-128 (+ ratelimit.py:41-52, store.py:262-270) — rate check (SELECT) and record (INSERT) are separate autocommit statements; one client's parallel uploads all pass (10/10 accepted at limit 6). CONFIRMED live.
2. [contract-divergence] ratelimit.py:35-38 — window looked up only under TODAY's hash; every client's window empties at 00:00 UTC (12 uploads in ~11 min). CONFIRMED.
3. [correctness] cut.py:48-49 vs sandbox.py:20 / cut.py:191-199 — output budget (≤ ~1.95 GiB) exceeds the task's 1 GiB RLIMIT_FSIZE; the uncounted ZIP then hits EFBIG → failed/resources (wrong message) and section PDFs are left in work/. CONFIRMED.
4. [ac-gap] routes/plan.py:92 — PUT /plan's write of plan.json after a concurrent DELETE → FileNotFoundError → 500 instead of 410. CONFIRMED.
Deployment note → STORY-013: `pdf-splitter api` runs uvicorn with proxy_headers=True / FORWARDED_ALLOW_IPS=127.0.0.1 — loopback peers' XFF is trusted by uvicorn itself.
