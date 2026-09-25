# Review — STORY-008
**Date:** 2026-09-25
**Reviewed:** pdf-splitter a9044de (feature/mvp)

## Round 1 — FAILED (2 confirmed, 1 refuted)
Coverage highlights: Bun-only (single text bun.lock, frozen install reproducible), 8 dev-only deps all from the default registry; no {@html}; no referrer leak in prod builds, no third-party requests, no console/storage use; polling non-overlapping, stops on terminal/404/410, cleaned up on unmount; errors.ts covers every API code (test reads errors.py).

1. [correctness] web/src/components/JobStatus.svelte:70-71 (hiccup :37, only rendered :106-108) — before the first successful poll every transient error is swallowed: "Loading…" forever, never an error/"Retrying…" (Architecture § web Failure mode). CONFIRMED (vitest probe + headless Chromium with API down).
2. [contract-divergence] errors.ts:12 no_text_layer wording vs PRD AC-8's example — REFUTED (AC-8's criterion is "explanatory message"; the quoted text is illustrative). Orchestrator copy decision anyway: say OCR isn't supported (see retry kickoff).
3. [correctness] JobStatus.svelte:63,67 — `active`/aria-busy ignores `fatal`: after 404/410 following a running status, aria-busy stays "true" forever. CONFIRMED.
