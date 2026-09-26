/**
 * A handful of routes, no router library: `/` (upload), `/j/<id>` (a job) and the static `/privacy` and `/terms`. The job id is the only credential
 * (ADR-007), so it lives in the URL and nowhere else: no storage, no title, no logging.
 */
export type Route = { name: 'home' } | { name: 'job'; id: string } | { name: 'privacy' } | { name: 'terms' } | { name: 'not_found' }

// Job ids are `secrets.token_urlsafe(16)`: 22 url-safe base64 chars. Accept any url-safe run so a future id
// length change does not strand links; the API answers 404 for ids it never issued.
const JOB_PATH = /^\/j\/([A-Za-z0-9_-]{1,64})\/?$/

export function parseRoute(pathname: string): Route {
  if (pathname === '/' || pathname === '') return { name: 'home' }
  if (pathname === '/privacy' || pathname === '/privacy/') return { name: 'privacy' }
  if (pathname === '/terms' || pathname === '/terms/') return { name: 'terms' }
  const m = JOB_PATH.exec(pathname)
  return m?.[1] ? { name: 'job', id: m[1] } : { name: 'not_found' }
}

export function jobPath(id: string): string {
  return `/j/${encodeURIComponent(id)}`
}

/** An in-app link's click handler: a plain left click navigates without a reload; a modified click opens a tab as usual. */
export function linkClick(e: MouseEvent, pathname: string): void {
  if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
  e.preventDefault()
  navigate(pathname)
}

type Listener = (pathname: string) => void
const listeners = new Set<Listener>()

export function navigate(pathname: string): void {
  if (pathname !== location.pathname) history.pushState(null, '', pathname)
  for (const l of listeners) l(location.pathname)
}

/** Calls `listener` on `navigate()` and on back/forward; returns the unsubscribe. */
export function onNavigate(listener: Listener): () => void {
  const onPop = () => listener(location.pathname)
  listeners.add(listener)
  window.addEventListener('popstate', onPop)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('popstate', onPop)
  }
}
