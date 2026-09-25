# Review — STORY-004
**Date:** 2026-09-25
**Reviewed:** pdf-splitter c69a615..20e34fa (feature/mvp)

## Round 1 — FAILED (1 confirmed)
Coverage highlights: schema = Architecture column-for-column; claim_next atomic under 8/16 PROCESSES (300–1000 jobs, same-second timestamps), WAL + busy_timeout behave; ids never logged; settings env parsing strict; health numbers correct.

1. [correctness] src/pdf_splitter/app.py:19-26 (+ store.py:66, :78-81) — `get_store` is a sync generator dependency; the Store's sqlite3 connection opens lazily in the endpoint's worker thread with default `check_same_thread=True`, and FastAPI runs the teardown (`store.close()`) via a SEPARATE threadpool call, often on another thread → `sqlite3.ProgrammingError` at store.py:80 before `self._conn = None` → connection never closed. Live: 600 req @ c64 → 585–2060 ASGI exception tracebacks; keep-alive clients lose ~37–48% of requests (RemoteDisconnected); ~450 leaked fds on jobs.db/-wal/-shm after one burst. Control `check_same_thread=False` → 0 errors. CONFIRMED by skeptic (repros: scratchpad/s004/burst.py, keepalive.py, patched_server.py).

Forward note (not a finding, for STORY-005): uvicorn's default access log will write raw `/api/jobs/{id}` paths — the id is the only credential.

## Round 2 (narrow re-review of 3da3255) — PASSED
Auto-review: 1 finding confirmed, fixed in 3da3255, re-review clean. Live: 600@c64 ×3, 2000@c128, 3000@c64 all 200; keep-alive 64 clients 12,800/12,800; 0 errors, jobs.db* fds back to 0. Control on 20e34fa still reproduces (1559 dropped, 592 leaked fds). Mutation check: each half of the fix has its own failing test. No connection is ever used by two threads concurrently (enter/endpoint/exit are sequential threadpool calls).
