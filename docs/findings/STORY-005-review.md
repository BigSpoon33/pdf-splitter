# Review — STORY-005
**Date:** 2026-09-25
**Reviewed:** pdf-splitter b84c3c4 (feature/mvp)

## Round 1 — FAILED (3 confirmed, 1 refuted-as-out-of-scope)
Coverage highlights: 210 MB → 413 in 0.7 s with flat RSS; nested-XObject hang PDF killed at 10.0 s, no zombies; mid-upload disconnect leaves nothing; 24 concurrent uploads fine; malformed-PDF corpus all → clean codes; filename never used as a path.

1. [correctness] access_log.py:16 (+ :19-20, :35) — `redact_path` only hashes an id directly after a literal, case-sensitive `^/api/jobs/`; `//api/jobs/<id>`, `/api/jobs//<id>`, `/api//jobs/<id>`, `/API/jobs/<id>`, `/./`, `/../jobs/`, absolute-form targets all log the RAW id (violates ADR-007 + binding addendum). CONFIRMED live.
2. [contract-divergence] Architecture § api "Unexpected errors → 500 with a request id" — REFUTED as a STORY-005 defect (not in its ACs; cross-cutting, unscheduled). → Scheduled into STORY-007 as AC-7 by the orchestrator.
3. [correctness] access_log.py:32-38 — logs the percent-DECODED `request.url.path` unescaped (uvicorn's logger quoted it) → terminal/log injection (`%1B%5B1A%1B%5B2K` erases lines under `tail -f`; U+2028/U+0085 raw). CONFIRMED live.
4. [correctness] upload.py:73 — preflight argv has no `--` and `jobs_dir` is never resolved; `PDFSPLIT_JOBS_DIR=.`/empty makes 1-in-64 ids (leading `-`) parse as an option → valid PDF gets 400 `unreadable`. CONFIRMED (dash.py).

Non-finding noted for later: preflight has no memory rlimit (ADR-005 said timeout-only); a 2.5 KB nested-XObject text PDF reaches ~790 MB RSS inside the 10 s window → STORY-006 addendum applies the worker's rlimits to preflight too.
