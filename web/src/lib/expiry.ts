/** How long until `expiresAt` (the API's ISO `expires_at`), as a visitor reads it; null once it has passed. */
export function timeLeft(expiresAt: string, now: number): string | null {
  const ms = Date.parse(expiresAt) - now
  if (!Number.isFinite(ms) || ms <= 0) return null
  const minutes = Math.floor(ms / 60_000)
  // Floored, so a fresh upload reads "23 h": the line promises no more time than is left.
  if (minutes >= 60) return `${Math.floor(minutes / 60)} h`
  if (minutes >= 1) return `${minutes} min`
  return 'less than a minute'
}

/** A file size for the results list. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
