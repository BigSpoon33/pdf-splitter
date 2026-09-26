const MB = 1024 * 1024

/** Mirrors the API's `PDFSPLIT_MAX_BYTES` default; the API re-checks, this only saves a doomed upload. */
export const MAX_BYTES = Number(import.meta.env.VITE_MAX_BYTES) || 200 * MB

/** Architecture § web: JobStatus polls this often while a job is queued or running. */
export const POLL_MS = 1500

/** AC-2: edits to the Plan are PUT this long after the last keystroke. */
export const SAVE_DEBOUNCE_MS = 600

/** How long the "Undo" toast stays after a source switch replaced the section list. */
export const UNDO_MS = 8000
