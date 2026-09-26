/**
 * A remaining duration as a visitor reads it. It never runs out on its own: at zero the files are deleted "in less
 * than a minute" until the API, whose clock set the duration, answers 410 (gate r1: the visitor's clock has no say).
 */
export function timeLeft(ms: number): string {
  const minutes = Math.floor(Math.max(ms, 0) / 60_000)
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
