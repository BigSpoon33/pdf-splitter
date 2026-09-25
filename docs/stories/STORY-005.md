# STORY-005: Web: upload endpoint with preflight

> **Status:** Done (2026-09-25)
> **Size:** M
> **Date:** 2026-09-25
> **Architecture ref:** `docs/Architecture.md#api`
> **Repo:** `~/Documents/Repos/pdf-splitter`

---

## Summary

As a visitor, I need to upload a PDF and have bad files refused immediately with a clear reason, so that I don't wait for a job that will fail.

---

## Context

Entry point of the service; first contact with untrusted input.

---

## Depends On

- STORY-004

---

## Acceptance Criteria

- [ ] AC-1: `POST /api/jobs` streams the upload to `/jobs/<id>/source.pdf.part` and aborts at `MAX_BYTES` with 413 `too_large` (never buffers the whole file in memory)
- [ ] AC-2: Preflight runs in a subprocess with a 10 s timeout: magic bytes `%PDF-` else 400 `not_pdf`; `doc.needs_pass` → 400 `encrypted`; `page_count > MAX_PAGES` → 413 `too_many_pages`; text probe (chars on up to 12 evenly spaced pages < 50 total) → 400 `no_text_layer`; any crash/timeout → 400 `unreadable`
- [ ] AC-3: Success: rename to `source.pdf`, create job row (state `queued`, kind `analyze`, sanitized filename ≤ 120 chars), respond 201 `{id, state}`
- [ ] AC-4: Every rejection deletes the partial file and creates no job row
- [ ] AC-5: Tests cover each rejection with generated fixtures (tiny PDFs built by pymupdf in the test, an encrypted one, an image-only one, a `.png` renamed)

---

## Files Affected

| File | Change Type | Notes |
|------|-------------|-------|
| `src/pdf_splitter/upload.py` | Create |  |
| `src/pdf_splitter/preflight.py` | Create | subprocess entry |
| `src/pdf_splitter/app.py` | Modify | route |
| `tests/test_upload.py` | Create |  |

---

## Implementation Notes

- Use `request.stream()` + manual multipart? Simpler: `UploadFile` spools to disk after 1 MB — acceptable, but enforce size while copying and set Caddy's max body too (STORY-013).
- Run preflight via `python -m pdf_splitter.preflight <path>` printing JSON.

---

## Out of Scope

- Rate limiting (STORY-012), analysis.

---

## Verification Steps

```bash
uv run pytest tests/test_upload.py
```

---

## E2E Test Plan

| Test | Type | What It Verifies |
|------|------|------------------|
| `tests/test_upload.py` | New | AC-1..5 |

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
feat: STORY-005 - upload with streaming size cap and sandboxed preflight
```

---

## Status

**Done** (2026-09-25)

- [x] AC-1
- [x] AC-2
- [x] AC-3
- [x] AC-4
- [x] AC-5
