const MB = 1024 * 1024

/** Mirrors the API's `PDFSPLIT_MAX_BYTES` default; the API re-checks, this only saves a doomed upload. */
export const MAX_BYTES = Number(import.meta.env.VITE_MAX_BYTES) || 200 * MB

/** Architecture § web: JobStatus polls this often while a job is queued or running. */
export const POLL_MS = 1500
