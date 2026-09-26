import { render, screen } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.svelte'
import type { JobStatus, Plan } from './lib/api'
import { SAVE_DEBOUNCE_MS } from './lib/config'
import { analysisOf, planOf, rowOf } from './lib/fixtures'

const ID = 'AbCdEfGhIjKlMnOpQrStUv'

/**
 * The API behind the real `fetch`, by URL: a job in `done` (edited, then cut) that answers every read and records
 * every write. The whole app runs on top of it — the router included — so the URL is what a visitor would open.
 */
function fakeServer(plan: Plan) {
  const status: JobStatus = {
    id: ID,
    state: 'done',
    kind: 'cut',
    progress: 3,
    total: 3,
    queue_position: null,
    message: null,
    error_code: null,
    expires_at: new Date(Date.now() + 23 * 3_600_000).toISOString(),
    seconds_left: 23 * 3600,
    filename: 'My Book.pdf',
    pages: 6,
  }
  const rows = plan.sections.map((s, i) => rowOf(i, s.name))
  const writes: string[] = []
  const json = (body: unknown, code = 200) => new Response(JSON.stringify(body), { status: code })
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    if (method !== 'GET') {
      writes.push(`${method} ${url}`)
      return json({ code: 'invalid', message: 'unexpected write' }, 422)
    }
    if (url === `/api/jobs/${ID}`) return json(status)
    if (url === `/api/jobs/${ID}/analysis`) return json(analysisOf())
    if (url === `/api/jobs/${ID}/plan`) return json(plan)
    if (url === `/api/jobs/${ID}/manifest`) return json(rows)
    return json({ code: 'not_found', message: 'no' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, writes }
}

function open(url: string) {
  history.replaceState(null, '', url)
  return render(App)
}

/** Longer than the editor's debounce: a save the load had queued would have gone out by now. */
const settle = () => new Promise((r) => setTimeout(r, SAVE_DEBOUNCE_MS + 200))

afterEach(() => {
  vi.unstubAllGlobals()
  history.replaceState(null, '', '/')
})

describe('App (gate r1: the URL never changes a job)', () => {
  it('a chapter job — edited and cut — opened with ?mode=ranges is the chapter job: chapter UI, its list, no PUT', async () => {
    const plan = planOf({ source: 'manual', sections: [{ name: 'Renamed by hand', page: 2, heading: '' }] })
    const { fetchMock, writes } = fakeServer(plan)
    open(`/j/${ID}?mode=ranges`)
    await vi.waitFor(() => expect(screen.getByLabelText('Outline')).toBeTruthy())
    expect(document.querySelector('.review')?.getAttribute('data-mode')).toBe('chapters')
    expect((screen.getByLabelText('Name of section 1') as HTMLInputElement).value).toBe('Renamed by hand')
    expect(screen.queryByLabelText(/pages to keep/i)).toBeNull()
    expect(screen.getByRole('link', { name: '001 - Renamed by hand.pdf' })).toBeTruthy()
    await settle()
    expect(writes).toEqual([])
    expect(fetchMock.mock.calls.every(([, init]) => (init?.method ?? 'GET') === 'GET')).toBe(true)
    expect(location.pathname + location.search).toBe(`/j/${ID}?mode=ranges`)
  })

  it.each(['', '?mode=ranges', '?mode=chapters'])('a ranges upload opens in range mode from its saved plan, with "%s" on the link, and writes nothing', async (query) => {
    const plan = planOf({ source: 'ranges', sections: [{ name: 'Pages 1–3', page: 1, heading: '', endPage: 3 }] })
    const { writes } = fakeServer(plan)
    open(`/j/${ID}${query}`)
    await vi.waitFor(() => expect(screen.getByLabelText(/pages to keep/i)).toBeTruthy())
    expect((screen.getByLabelText(/pages to keep/i) as HTMLInputElement).value).toBe('1-3')
    expect(document.querySelector('.review')?.getAttribute('data-mode')).toBe('ranges')
    expect(screen.queryByLabelText('Outline')).toBeNull()
    await settle()
    expect(writes).toEqual([])
  })
})
