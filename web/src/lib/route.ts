/**
 * A handful of routes, no router library: `/` (upload), `/j/<id>` (a job) and the static `/privacy` and `/terms`. The job id is the only credential
 * (ADR-007), so it lives in the URL and nowhere else: no storage, no title, no logging.
 *
 * `/j/<id>?mode=ranges` (ADR-009) is the mode the home page chose, carried through the upload redirect until the
 * job's plan says `source: "ranges"` itself; the query is the only part of the URL that is read.
 */
export type JobMode = 'ranges'
export type Route = { name: 'home' } | { name: 'job'; id: string; mode?: JobMode } | { name: 'privacy' } | { name: 'terms' } | { name: 'not_found' }

// Job ids are `secrets.token_urlsafe(16)`: 22 url-safe base64 chars. Accept any url-safe run so a future id
// length change does not strand links; the API answers 404 for ids it never issued.
const JOB_PATH = /^\/j\/([A-Za-z0-9_-]{1,64})\/?$/

/** `path` is the pathname with an optional `?query` (what `href()` gives): the query only matters on a job. */
export function parseRoute(path: string): Route {
  const at = path.indexOf('?')
  const pathname = at === -1 ? path : path.slice(0, at)
  if (pathname === '/' || pathname === '') return { name: 'home' }
  if (pathname === '/privacy' || pathname === '/privacy/') return { name: 'privacy' }
  if (pathname === '/terms' || pathname === '/terms/') return { name: 'terms' }
  const m = JOB_PATH.exec(pathname)
  if (!m?.[1]) return { name: 'not_found' }
  const mode = at === -1 ? null : new URLSearchParams(path.slice(at + 1)).get('mode')
  return mode === 'ranges' ? { name: 'job', id: m[1], mode } : { name: 'job', id: m[1] }
}

export function jobPath(id: string, mode?: JobMode): string {
  return `/j/${encodeURIComponent(id)}${mode ? `?mode=${mode}` : ''}`
}

/** The part of the location the router reads: pathname plus query, never the hash. */
export function href(): string {
  return location.pathname + location.search
}

/** An in-app link's click handler: a plain left click navigates without a reload; a modified click opens a tab as usual. */
export function linkClick(e: MouseEvent, pathname: string): void {
  if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
  e.preventDefault()
  navigate(pathname)
}

type Listener = (path: string) => void
const listeners = new Set<Listener>()

export function navigate(path: string): void {
  if (path !== href()) history.pushState(null, '', path)
  for (const l of listeners) l(href())
}

/** Calls `listener` on `navigate()` and on back/forward with the new `href()`; returns the unsubscribe. */
export function onNavigate(listener: Listener): () => void {
  const onPop = () => listener(href())
  listeners.add(listener)
  window.addEventListener('popstate', onPop)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('popstate', onPop)
  }
}
