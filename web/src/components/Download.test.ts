import { fireEvent, render, screen } from '@testing-library/svelte'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, type CreatedJob, type JobStatus, type Plan } from '../lib/api'
import { PlanEditor } from '../lib/editor.svelte'
import { MESSAGES } from '../lib/errors'
import { planOf, rowOf } from '../lib/fixtures'
import Download from './Download.svelte'

const ID = 'AbCdEfGhIjKlMnOpQrStUv'

function status(over: Partial<JobStatus>): JobStatus {
  return {
    id: ID,
    state: 'review',
    kind: 'analyze',
    progress: 0,
    total: 0,
    queue_position: null,
    message: null,
    error_code: null,
    expires_at: '2026-09-26T12:00:00+00:00',
    seconds_left: 84_600,
    filename: 'My Book.pdf',
    pages: 6,
    ...over,
  }
}

const ROWS = [
  rowOf(0, '1 Foundations of Testing', ['heading-not-found']),
  rowOf(1, '2 Chapter Two', []),
  rowOf(2, '3 Closing Chapter', ['span-clamped']),
]

function mount(
  over: {
    job?: JobStatus | null
    results?: typeof ROWS | null
    resultsError?: string | null
    stale?: boolean
    cut?: (id: string) => Promise<CreatedJob>
    save?: (plan: Plan) => Promise<Plan>
  } = {},
) {
  const save = vi.fn(over.save ?? (async (plan: Plan) => plan))
  const editor = new PlanEditor(planOf(), save, { debounceMs: 10_000 })
  const cut = vi.fn(over.cut ?? (async () => ({ id: ID, state: 'queued' as const })))
  const oncut = vi.fn()
  const onretry = vi.fn()
  const ongone = vi.fn()
  const utils = render(Download, {
    id: ID,
    editor,
    job: over.job === undefined ? status({}) : over.job,
    results: over.results ?? null,
    resultsError: over.resultsError ?? null,
    stale: over.stale ?? false,
    cut,
    oncut,
    onretry,
    ongone,
  })
  return { editor, save, cut, oncut, onretry, ongone, ...utils }
}

const splitButton = () => screen.getByRole('button', { name: /^Split into/ }) as HTMLButtonElement

describe('Download', () => {
  it('Split saves a pending edit first, then posts the cut and asks the page to follow it (AC-1)', async () => {
    const { editor, save, cut, oncut } = mount()
    expect(splitButton().textContent?.trim()).toBe('Split into 3 PDFs')
    editor.rename(0, 'Renamed')
    await fireEvent.click(splitButton())
    await vi.waitFor(() => expect(oncut).toHaveBeenCalledTimes(1))
    expect(save).toHaveBeenCalledTimes(1)
    expect(save.mock.invocationCallOrder[0]!).toBeLessThan(cut.mock.invocationCallOrder[0]!)
    expect(cut).toHaveBeenCalledWith(ID)
    expect(editor.edited).toBe(false)
  })

  it('does not cut a plan the API refused, and says why', async () => {
    const { editor, cut } = mount({ save: async () => Promise.reject(new ApiError(409, 'busy')) })
    editor.rename(0, 'Renamed')
    await fireEvent.click(splitButton())
    await vi.waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/not been saved/))
    expect(cut).not.toHaveBeenCalled()
  })

  it('a busy answer to our own cut is not an error: the cut is followed like any other (gate r1 F3)', async () => {
    const { oncut } = mount({ cut: async () => Promise.reject(new ApiError(409, 'busy')) })
    await fireEvent.click(splitButton())
    await vi.waitFor(() => expect(oncut).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('alert')).toBeNull()
    expect(splitButton().disabled).toBe(true)
  })

  it('a 410 from the cut hands the page to the deleted screen', async () => {
    const { ongone } = mount({ cut: async () => Promise.reject(new ApiError(410, 'expired')) })
    await fireEvent.click(splitButton())
    await vi.waitFor(() => expect(ongone).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('one split per click: Split stays disabled from the click until the poll reports the cut, so a second click posts nothing (gate r1 F3)', async () => {
    const { cut, oncut, rerender } = mount({ job: status({ state: 'done', kind: 'cut' }), results: ROWS })
    await fireEvent.click(splitButton())
    await vi.waitFor(() => expect(oncut).toHaveBeenCalledTimes(1))
    // The 202 is back, no poll has answered yet: this is the gap a double-click used to fall into.
    expect(splitButton().disabled).toBe(true)
    await fireEvent.click(splitButton())
    await fireEvent.click(splitButton())
    expect(cut).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('alert')).toBeNull()

    await rerender({ job: status({ state: 'queued', kind: 'cut' }) })
    expect(splitButton().disabled).toBe(true)
    await rerender({ job: status({ state: 'done', kind: 'cut' }) })
    expect(splitButton().disabled).toBe(false)
    await fireEvent.click(splitButton())
    await vi.waitFor(() => expect(cut).toHaveBeenCalledTimes(2))
  })

  it('a split refused by the API frees the button again, and the message goes once a later status arrives (gate r1 F3)', async () => {
    const { cut, rerender } = mount({ cut: async () => Promise.reject(new ApiError(500, 'internal')) })
    await fireEvent.click(splitButton())
    await vi.waitFor(() => expect(screen.getByRole('alert').textContent).toBe(MESSAGES.internal))
    expect(splitButton().disabled).toBe(false)
    await fireEvent.click(splitButton())
    await vi.waitFor(() => expect(cut).toHaveBeenCalledTimes(2))
    await rerender({ job: status({ state: 'review', kind: 'analyze' }) })
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('a results list that could not be loaded is an error with Retry, never the previous files (gate r1 F2)', async () => {
    const { onretry } = mount({ job: status({ state: 'done', kind: 'cut' }), results: null, resultsError: MESSAGES.internal })
    expect(screen.queryByRole('link', { name: 'Download all (ZIP)' })).toBeNull()
    expect(screen.getByRole('alert').textContent).toContain(MESSAGES.internal)
    await fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onretry).toHaveBeenCalledTimes(1)
  })

  it('while cutting: Split is disabled, progress is per section, the old results are hidden (AC-1)', () => {
    mount({ job: status({ state: 'running', kind: 'cut', progress: 1, total: 3, message: 'Cutting sections' }), results: ROWS })
    expect(splitButton().disabled).toBe(true)
    expect(screen.getByText('Cutting section 2 of 3…')).toBeTruthy()
    expect(screen.queryByRole('link', { name: 'Download all (ZIP)' })).toBeNull()
  })

  it('lists every file with its link, size and flags, plus the ZIP (AC-1); downloads are plain links, never fetched', () => {
    mount({ job: status({ state: 'done', kind: 'cut' }), results: ROWS })
    expect(screen.getByRole('heading', { name: 'Your files' })).toBeTruthy()
    const zip = screen.getByRole('link', { name: 'Download all (ZIP)' })
    expect(zip.getAttribute('href')).toBe(`/api/jobs/${ID}/result.zip`)
    expect(zip.hasAttribute('download')).toBe(true)
    const links = screen.getAllByRole('link').filter((a) => a !== zip)
    expect(links.map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
      ['001 - 1 Foundations of Testing.pdf', `/api/jobs/${ID}/sections/0.pdf`],
      ['002 - 2 Chapter Two.pdf', `/api/jobs/${ID}/sections/1.pdf`],
      ['003 - 3 Closing Chapter.pdf', `/api/jobs/${ID}/sections/2.pdf`],
    ])
    expect(links.every((a) => a.hasAttribute('download'))).toBe(true)
    // The last file always reaches the end of the book: its span-clamped is not a warning.
    expect([...document.querySelectorAll('.badge')].map((b) => b.textContent)).toEqual(['Heading not found on its page'])
    expect(screen.getAllByText('1000 B')).toHaveLength(3)
  })

  it('keeps the results after an edit, titled as the last cut (AC-2)', () => {
    mount({ job: status({ state: 'review', kind: 'cut' }), results: ROWS, stale: true })
    expect(screen.getByRole('heading', { name: 'Files from the last cut' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Download all (ZIP)' })).toBeTruthy()
    expect(splitButton().disabled).toBe(false)
  })

  it('a failed cut disables Split and says the job cannot be cut again', () => {
    mount({ job: status({ state: 'failed', kind: 'cut', error_code: 'timeout' }) })
    expect(splitButton().disabled).toBe(true)
    expect(screen.getByRole('alert').textContent).toMatch(/can't be split again/)
  })
})
