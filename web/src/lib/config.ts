const MB = 1024 * 1024

/** Mirrors the API's `PDFSPLIT_MAX_BYTES` default; the API re-checks, this only saves a doomed upload. */
export const MAX_BYTES = Number(import.meta.env.VITE_MAX_BYTES) || 200 * MB

/** Architecture § web: JobStatus polls this often while a job is queued or running. */
export const POLL_MS = 1500

/** AC-2: edits to the Plan are PUT this long after the last keystroke. */
export const SAVE_DEBOUNCE_MS = 600

/** How long the "Undo" toast stays after a source switch replaced the section list. */
export const UNDO_MS = 8000

/**
 * Chromium refuses `fetch(..., {keepalive: true})` bodies over 64 KiB (the in-flight keepalive budget), so a
 * plan larger than this goes out as an ordinary PUT on unload; a margin under 65,536 covers the headers it counts.
 */
export const KEEPALIVE_MAX_BYTES = 60_000

/** The sheet PNGs the preview shows (`GET /sheets/{n}.png?dpi=`): 110 keeps small type readable when the image is scaled down. */
export const PREVIEW_DPI = 110 as const

/** The "files deleted in 23 h" line counts in whole minutes at best, so it re-reads the clock this often. */
export const EXPIRY_TICK_MS = 60_000

/**
 * Once the countdown from `seconds_left` reaches zero the page asks the API once more; this margin lets the server's
 * whole-second deadline pass first, and paces the re-asks if it keeps answering 200 with nothing left.
 */
export const EXPIRY_GRACE_MS = 1500

/**
 * A page left open in `review`/`done` asks the API for its status again this often (gate r2): the countdown runs on
 * the monotonic clock, which stands still through a system suspend, so only a fresh `seconds_left` can say how much
 * of the 24 h the sleep took. Wake-up events (visibility, pageshow, online, a wall-clock jump) ask sooner.
 */
export const WAKE_RECHECK_MS = 5 * 60_000

/** A results refresh that fails after a cut is retried once by itself this long later, before the visitor has to. */
export const MANIFEST_RETRY_MS = 2000
