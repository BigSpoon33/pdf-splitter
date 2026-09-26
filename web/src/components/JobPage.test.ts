import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, type JobStatus, type ManifestRow, type Plan } from '../lib/api'
import { MESSAGES } from '../lib/errors'
import { analysisOf, planOf, rowOf } from '../lib/fixtures'
import JobPage from './JobPage.svelte'

const ID = 'AbCdEfGhIjKlMnOpQrStUv'
const HOUR = 3_600_000

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
    expires_at: new Date(Date.now() + 23.5 * HOUR).toISOString(),
    seconds_left: 23.5 * 3600,
    filename: 'My Book.pdf',
    pages: 6,
    ...over,
  }
}

const ROWS: ManifestRow[] = [
  rowOf(0, '1 Foundations of Testing', ['heading-not-found']),
  rowOf(1, '2 Chapter Two: The Middle of the Synthetic Book'),
  rowOf(2, '3 Closing Chapter'),
]

/**
 * A stand-in for the API's job row, following the transitions tests/test_api_e2e.py pins: a cut goes
 * queued → running → done (::test_cut_queues_once_and_resets_the_row, ::test_cut_from_done_recuts), a save from
 * `done` goes back to `review` (routes/plan.py:put_plan), everything answers 410 once deleted.
 */
function fakeApi(start: Partial<JobStatus> = {}) {
  let row = status(start)
  let phases: JobStatus[] = []
  let manifest: ManifestRow[] | null = null
  let deleted = false
  const gone = () => new ApiError(410, 'expired')
  const api = {
    /** What the next cut writes. */
    rows: ROWS,
    /**
     * The next cut's first status, when the poll cannot follow it: `review` is a cut that finished AND took an edit
     * before the page heard back (the r2 stranded-Split case).
     */
    straightTo: null as 'review' | 'done' | null,
    load: vi.fn(async () => {
      if (deleted) throw gone()
      const next = phases.shift()
      if (next) row = next
      return row
    }),
    save: vi.fn(async (_id: string, plan: Plan) => {
      if (deleted) throw gone()
      if (row.state === 'done') row = { ...row, state: 'review' }
      return plan
    }),
    cut: vi.fn(async () => {
      if (deleted) throw gone()
      row = { ...row, state: 'queued', kind: 'cut' }
      phases = api.straightTo
        ? [{ ...row, state: api.straightTo, progress: 3, total: 3, message: null }]
        : [
            row,
            { ...row, state: 'running', progress: 1, total: 3, message: 'Cutting sections' },
            { ...row, state: 'done', progress: 3, total: 3, message: null },
          ]
      manifest = api.rows
      return { id: ID, state: 'queued' as const }
    }),
    loadManifest: vi.fn(async () => {
      if (deleted) throw gone()
      if (!manifest) throw new ApiError(409, 'not_ready')
      return manifest
    }),
    remove: vi.fn(async () => {
      deleted = true
    }),
    deleteNow: () => {
      deleted = true
    },
  }
  return api
}

function mount(
  api: ReturnType<typeof fakeApi>,
  confirmDelete = vi.fn(() => true),
  page: { recheckMs?: number; tickMs?: number; mode?: 'ranges'; plan?: Plan } = {},
) {
  const { plan, ...rest } = page
  return render(JobPage, {
    id: ID,
    load: api.load,
    pollMs: 5,
    ...rest,
    review: {
      loadAnalysis: async () => analysisOf(),
      loadPlan: async () => plan ?? planOf(),
      loadManifest: api.loadManifest,
      loadSectionPlan: async () => Promise.reject(new ApiError(500, 'preview_failed')),
      loadSheet: async () => new Blob(),
      save: api.save,
      cut: api.cut,
      debounceMs: 10,
      retryMs: 300,
    },
    expiry: { remove: api.remove, confirmDelete, graceMs: 5 },
  })
}

afterEach(() => {
  vi.useRealTimers()
})

const stateShown = () => document.querySelector('[data-state]')?.getAttribute('data-state')
const splitButton = () => screen.getByRole('button', { name: /^Split into/ })
const goneScreen = () => document.querySelector('[data-gone]')?.getAttribute('data-gone')

describe('JobPage', () => {
  it('Split → the poll follows the cut to done → the results list comes from the new manifest (AC-1)', async () => {
    const api = fakeApi()
    mount(api)
    await vi.waitFor(() => expect(splitButton()).toBeTruthy())
    expect(screen.queryByRole('link', { name: 'Download all (ZIP)' })).toBeNull()
    await fireEvent.click(splitButton())
    await vi.waitFor(() => expect(stateShown()).toBe('done'))
    await vi.waitFor(() => expect(screen.getByRole('link', { name: 'Download all (ZIP)' })).toBeTruthy())
    expect(screen.getByRole('link', { name: '001 - 1 Foundations of Testing.pdf' }).getAttribute('href')).toBe(`/api/jobs/${ID}/sections/0.pdf`)
    expect(api.cut).toHaveBeenCalledTimes(1)
    // Once to see `review`, then queued → running → done; nothing after the terminal state.
    const polls = api.load.mock.calls.length
    await new Promise((r) => setTimeout(r, 50))
    expect(api.load.mock.calls.length).toBe(polls)
  })

  it('an edit after the cut takes the status back to review and keeps the downloads (AC-2)', async () => {
    const api = fakeApi()
    mount(api)
    await vi.waitFor(() => expect(splitButton()).toBeTruthy())
    await fireEvent.click(splitButton())
    await vi.waitFor(() => expect(screen.getByRole('heading', { name: 'Your files' })).toBeTruthy())
    expect(stateShown()).toBe('done')

    const name = screen.getByLabelText('Name of section 3')
    await fireEvent.input(name, { target: { value: 'The End' } })
    await fireEvent.blur(name)
    await vi.waitFor(() => expect(api.save).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(stateShown()).toBe('review'))
    expect(screen.getByRole('heading', { name: 'Files from the last cut' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Download all (ZIP)' })).toBeTruthy()
    expect(screen.getByText(/cut again to refresh the files/)).toBeTruthy()
  })

  it('a job reloaded in done shows the last results and the expiry line (AC-1, AC-3)', async () => {
    const api = fakeApi({ state: 'done', kind: 'cut' })
    await api.cut()
    api.load.mockImplementation(async () => status({ state: 'done', kind: 'cut' }))
    mount(api)
    await vi.waitFor(() => expect(screen.getByRole('heading', { name: 'Your files' })).toBeTruthy())
    expect(screen.getByText('Files deleted in 23 h.')).toBeTruthy()
    expect(screen.queryByText(/cut again to refresh the files/)).toBeNull()
  })

  it('Delete now asks first; confirmed, it deletes and the whole page becomes the deleted screen (AC-3)', async () => {
    const api = fakeApi()
    const confirmDelete = vi.fn(() => false)
    mount(api, confirmDelete)
    await vi.waitFor(() => expect(screen.getByText('Files deleted in 23 h.')).toBeTruthy())
    await fireEvent.click(screen.getByRole('button', { name: 'Delete now' }))
    expect(confirmDelete).toHaveBeenCalledTimes(1)
    expect(api.remove).not.toHaveBeenCalled()

    confirmDelete.mockReturnValue(true)
    await fireEvent.click(screen.getByRole('button', { name: 'Delete now' }))
    await vi.waitFor(() => expect(goneScreen()).toBe('deleted'))
    expect(api.remove).toHaveBeenCalledWith(ID)
    expect(screen.getByRole('heading', { name: 'Your files were deleted' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Split another PDF' }).getAttribute('href')).toBe('/')
    expect(document.querySelector('[data-state]')).toBeNull()
    expect(screen.queryByLabelText(/^Name of section/)).toBeNull()
    expect(screen.getAllByRole('link', { name: 'Split another PDF' })).toHaveLength(1)
  })

  it.each([
    [410, 'expired', 'This job was deleted', MESSAGES.expired],
    [404, 'not_found', 'There is no job here', MESSAGES.not_found],
  ] as const)('a %i at load is the deleted screen with a Split another PDF link (AC-4)', async (httpStatus, code, title, body) => {
    const api = fakeApi()
    api.load.mockRejectedValue(new ApiError(httpStatus, code))
    mount(api)
    await vi.waitFor(() => expect(goneScreen()).toBe(code))
    expect(screen.getByRole('heading', { name: title })).toBeTruthy()
    expect(screen.getByText(body)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Split another PDF' })).toBeTruthy()
  })

  it('a 410 mid-session (a save after the job was deleted elsewhere) wins over the editor error line (AC-4)', async () => {
    const api = fakeApi()
    mount(api)
    await vi.waitFor(() => expect(screen.getByLabelText('Name of section 1')).toBeTruthy())
    api.deleteNow()
    const name = screen.getByLabelText('Name of section 1')
    await fireEvent.input(name, { target: { value: 'Gone' } })
    await fireEvent.blur(name)
    await vi.waitFor(() => expect(goneScreen()).toBe('expired'))
    expect(screen.queryByText(MESSAGES.expired, { selector: '.error-inline' })).toBeNull()
  })

  it.each([25, -25])("the visitor's clock %i h off the server's: the countdown is the server's count and the page stays (gate r1 F1)", async (skewHours) => {
    // The row (and its expires_at) is the server's; only then does the visitor's Date go wrong.
    const api = fakeApi()
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(Date.now() + skewHours * HOUR)
    mount(api)
    await vi.waitFor(() => expect(screen.getByText('Files deleted in 23 h.')).toBeTruthy())
    await vi.waitFor(() => expect(splitButton()).toBeTruthy())
    await new Promise((r) => setTimeout(r, 30))
    expect(goneScreen()).toBeUndefined()
    expect(screen.getByText('Files deleted in 23 h.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Delete now' })).toBeTruthy()
  })

  it("the count running out asks the API once more: a fresh count keeps the page, it is never the clock's call (gate r1 F1)", async () => {
    const api = fakeApi({ seconds_left: 0, expires_at: new Date(Date.now() - 1000).toISOString() })
    api.load.mockImplementationOnce(async () => status({ seconds_left: 0 })).mockImplementation(async () => status({ seconds_left: 3600 }))
    mount(api)
    await vi.waitFor(() => expect(screen.getByText('Files deleted in 1 h.')).toBeTruthy())
    expect(api.load).toHaveBeenCalledTimes(2)
    await new Promise((r) => setTimeout(r, 30))
    expect(api.load).toHaveBeenCalledTimes(2)
    expect(goneScreen()).toBeUndefined()
  })

  it("the count running out asks the API once more: its 410 is the deleted screen (AC-4, gate r1 F1)", async () => {
    const api = fakeApi({ seconds_left: 0 })
    api.load.mockImplementationOnce(async () => status({ seconds_left: 0 })).mockRejectedValue(new ApiError(410, 'expired'))
    mount(api)
    await vi.waitFor(() => expect(goneScreen()).toBe('expired'))
    expect(api.load).toHaveBeenCalledTimes(2)
  })

  it("a results refresh that fails after a re-cut shows an error and retries by itself; the previous cut's files are never listed (gate r1 F2)", async () => {
    const api = fakeApi()
    mount(api)
    await vi.waitFor(() => expect(splitButton()).toBeTruthy())
    await fireEvent.click(splitButton())
    await vi.waitFor(() => expect(screen.getByRole('link', { name: '001 - 1 Foundations of Testing.pdf' })).toBeTruthy())
    await vi.waitFor(() => expect(splitButton().hasAttribute('disabled')).toBe(false))

    api.rows = [rowOf(0, '1 Foundations of Testing'), rowOf(1, '2 A Different Second Chapter')]
    api.loadManifest.mockImplementationOnce(async () => Promise.reject(new ApiError(502, 'internal')))
    await fireEvent.click(splitButton())
    await vi.waitFor(() => expect(screen.getByRole('alert').textContent).toContain(MESSAGES.internal))
    expect(stateShown()).toBe('done')
    expect(screen.queryByRole('link', { name: 'Download all (ZIP)' })).toBeNull()
    expect(screen.queryByRole('link', { name: '002 - 2 Chapter Two: The Middle of the Synthetic Book.pdf' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()

    await vi.waitFor(() => expect(screen.getByRole('link', { name: '002 - 2 A Different Second Chapter.pdf' })).toBeTruthy(), { timeout: 2000 })
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByRole('heading', { name: 'Your files' })).toBeTruthy()
    expect(api.loadManifest).toHaveBeenCalledTimes(4) // mount (409), cut 1, cut 2 (502), the automatic retry
  })

  it("a re-cut whose first status is already review (an edit saved before the poll could answer) frees Split and lists the new files (gate r2 F3)", async () => {
    const api = fakeApi()
    mount(api)
    await vi.waitFor(() => expect(splitButton()).toBeTruthy())
    await fireEvent.click(splitButton())
    await vi.waitFor(() => expect(screen.getByRole('heading', { name: 'Your files' })).toBeTruthy())
    const name = screen.getByLabelText('Name of section 3')
    await fireEvent.input(name, { target: { value: 'The End' } })
    await fireEvent.blur(name)
    await vi.waitFor(() => expect(stateShown()).toBe('review'))
    await vi.waitFor(() => expect(splitButton().hasAttribute('disabled')).toBe(false))

    // The poll is unreachable while cut 2 runs and a further edit is saved: the first status it gets is review/cut.
    api.rows = [rowOf(0, '1 Foundations of Testing'), rowOf(1, '2 Chapter Two'), rowOf(2, '3 The End')]
    api.straightTo = 'review'
    await fireEvent.click(splitButton())
    await fireEvent.click(splitButton())
    await vi.waitFor(() => expect(screen.getByRole('link', { name: '003 - 3 The End.pdf' })).toBeTruthy())
    expect(api.cut).toHaveBeenCalledTimes(2)
    expect(stateShown()).toBe('review')
    expect(screen.getByRole('heading', { name: 'Files from the last cut' })).toBeTruthy()
    expect(screen.queryByRole('link', { name: '003 - 3 Closing Chapter.pdf' })).toBeNull()
    await vi.waitFor(() => expect(splitButton().hasAttribute('disabled')).toBe(false))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  describe('after a suspend (gate r2 F1)', () => {
    /**
     * A system suspend stops the monotonic clock and every timer with it, while the server's clock (and the visitor's
     * wall clock) keep going: the page wakes up believing no time has passed. Here the API's next answer carries the
     * server's figure, `Date` jumps a day, and neither `performance.now()` nor the timers move.
     */
    async function suspended(api: ReturnType<typeof fakeApi>, hours: number, secondsLeft: number) {
      await vi.waitFor(() => expect(splitButton()).toBeTruthy())
      expect(screen.getByText('Files deleted in 23 h.')).toBeTruthy()
      vi.useFakeTimers({ toFake: ['Date'] })
      vi.setSystemTime(Date.now() + hours * HOUR)
      api.load.mockImplementation(async () => status({ seconds_left: secondsLeft }))
      return api.load.mock.calls.length
    }
    const wake = (type: string, target: EventTarget = window) => target.dispatchEvent(new Event(type))

    it("becoming visible again asks the API once, and the countdown shows the server's figure", async () => {
      const api = fakeApi()
      mount(api)
      const polls = await suspended(api, 24, 30 * 60)
      wake('visibilitychange', document)
      await vi.waitFor(() => expect(screen.getByText('Files deleted in 30 min.')).toBeTruthy())
      expect(api.load).toHaveBeenCalledTimes(polls + 1)
      await new Promise((r) => setTimeout(r, 30))
      expect(api.load).toHaveBeenCalledTimes(polls + 1)
      expect(goneScreen()).toBeUndefined()
      expect(screen.getByRole('button', { name: 'Delete now' })).toBeTruthy()
    })

    it('a 410 from that re-check is the deleted screen; the countdown alone never is', async () => {
      const api = fakeApi()
      mount(api)
      const polls = await suspended(api, 25, 0)
      api.load.mockRejectedValue(new ApiError(410, 'expired'))
      expect(goneScreen()).toBeUndefined()
      wake('pageshow')
      await vi.waitFor(() => expect(goneScreen()).toBe('expired'))
      expect(api.load).toHaveBeenCalledTimes(polls + 1)
    })

    it('one poll per wake-up event, none while hidden, none while the job is still running', async () => {
      const api = fakeApi()
      mount(api)
      const polls = await suspended(api, 1, 22 * 3600)
      wake('visibilitychange', document)
      await vi.waitFor(() => expect(api.load).toHaveBeenCalledTimes(polls + 1))
      wake('pageshow')
      await vi.waitFor(() => expect(api.load).toHaveBeenCalledTimes(polls + 2))
      wake('online')
      await vi.waitFor(() => expect(api.load).toHaveBeenCalledTimes(polls + 3))
      await new Promise((r) => setTimeout(r, 30))
      expect(api.load).toHaveBeenCalledTimes(polls + 3)

      Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
      wake('visibilitychange', document)
      await new Promise((r) => setTimeout(r, 30))
      expect(api.load).toHaveBeenCalledTimes(polls + 3)
      Reflect.deleteProperty(document, 'visibilityState')

      // Mid-cut the loop polls by itself: a wake-up must not restart it on top.
      let hung = 0
      api.load
        .mockImplementationOnce(async () => status({ state: 'running', kind: 'cut', progress: 1, total: 3 }))
        .mockImplementation(() => new Promise<JobStatus>(() => hung++))
      wake('online')
      await vi.waitFor(() => expect(hung).toBe(1))
      wake('online')
      wake('pageshow')
      wake('visibilitychange', document)
      await new Promise((r) => setTimeout(r, 30))
      expect(hung).toBe(1)
    })

    it('a wake-up that fires no event is caught by the wall clock outrunning the monotonic one: one re-check, then nothing', async () => {
      const api = fakeApi()
      mount(api, undefined, { recheckMs: 10_000, tickMs: 10 })
      await vi.waitFor(() => expect(splitButton()).toBeTruthy())
      const polls = api.load.mock.calls.length
      await new Promise((r) => setTimeout(r, 60))
      expect(api.load).toHaveBeenCalledTimes(polls)
      await suspended(api, 24, 45 * 60)
      await vi.waitFor(() => expect(screen.getByText('Files deleted in 45 min.')).toBeTruthy())
      expect(api.load).toHaveBeenCalledTimes(polls + 1)
      await new Promise((r) => setTimeout(r, 60))
      expect(api.load).toHaveBeenCalledTimes(polls + 1)
    })

    it('a settled page re-checks by itself every recheckMs, and each answer re-anchors the countdown', async () => {
      const api = fakeApi()
      mount(api, undefined, { recheckMs: 40, tickMs: 10 })
      await vi.waitFor(() => expect(splitButton()).toBeTruthy())
      const polls = api.load.mock.calls.length
      api.load.mockImplementation(async () => status({ seconds_left: 5 * 3600 }))
      await vi.waitFor(() => expect(api.load.mock.calls.length).toBeGreaterThanOrEqual(polls + 2), { timeout: 1000 })
      expect(screen.getByText('Files deleted in 5 h.')).toBeTruthy()
    })
  })
})

describe('page-range mode (STORY-015, ADR-009)', () => {
  const RANGE_ROWS: ManifestRow[] = [rowOf(0, 'Pages 1–3'), rowOf(1, 'Page 5')]
  const field = () => screen.getByLabelText(/pages to keep/i) as HTMLInputElement

  it('a job opened with ?mode=ranges saves an empty ranges plan, shows the range editor and no chapter tools; typing ranges enables Split → cut → files (AC-1, AC-5)', async () => {
    const api = fakeApi()
    api.rows = RANGE_ROWS
    mount(api, undefined, { mode: 'ranges' })
    await vi.waitFor(() => expect(field()).toBeTruthy())
    expect(document.querySelector('.review')?.getAttribute('data-mode')).toBe('ranges')
    expect(screen.queryByLabelText('Outline')).toBeNull()
    expect(screen.queryByText('Layout')).toBeNull()
    expect(screen.queryByText('Select a section to preview where it will be cut.')).toBeNull()
    expect(screen.queryByLabelText('Select section 1')).toBeNull()
    // The chapter plan the upload analyzed to became an empty ranges plan, saved at once.
    await vi.waitFor(() => expect(api.save).toHaveBeenCalledTimes(1))
    expect(api.save.mock.calls[0]?.[1]).toMatchObject({ source: 'ranges', sections: [], overrides: {} })
    expect(field().value).toBe('')
    expect((splitButton() as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText('No ranges yet. Type them above.')).toBeTruthy()

    await fireEvent.input(field(), { target: { value: '1-3, 5' } })
    expect(screen.getByText('2 sections')).toBeTruthy()
    expect(screen.getByLabelText('Pages of section 1').textContent?.replace(/\s+/g, ' ').trim()).toBe('p. 1–3 · 3 pages')
    expect(screen.getByLabelText('Pages of section 2').textContent?.replace(/\s+/g, ' ').trim()).toBe('p. 5 · 1 page')
    expect(screen.queryByLabelText('Start page of section 1')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Merge ↓' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Add section' })).toBeNull()
    await vi.waitFor(() => expect(api.save).toHaveBeenCalledTimes(2))
    expect(api.save.mock.calls[1]?.[1].sections).toEqual([
      { name: 'Pages 1–3', page: 1, heading: '', endPage: 3 },
      { name: 'Page 5', page: 5, heading: '', endPage: 5 },
    ])
    await vi.waitFor(() => expect((splitButton() as HTMLButtonElement).disabled).toBe(false))
    expect(splitButton().textContent?.trim()).toBe('Split into 2 PDFs')
    await fireEvent.click(splitButton())
    await vi.waitFor(() => expect(stateShown()).toBe('done'))
    await vi.waitFor(() => expect(screen.getByRole('link', { name: 'Download all (ZIP)' })).toBeTruthy())
    expect(screen.getByRole('link', { name: '001 - Pages 1–3.pdf' }).getAttribute('href')).toBe(`/api/jobs/${ID}/sections/0.pdf`)
    expect(screen.getByRole('link', { name: '002 - Page 5.pdf' })).toBeTruthy()
    expect(api.cut).toHaveBeenCalledTimes(1)
    expect(document.querySelectorAll('.badge')).toHaveLength(0)
  })

  it('a reload without the query keeps the mode from the saved plan (AC-1) and shows its ranges', async () => {
    const api = fakeApi({ state: 'done', kind: 'cut' })
    const plan = planOf({
      source: 'ranges',
      sections: [
        { name: 'Pages 1–3', page: 1, heading: '', endPage: 3 },
        { name: 'Page 5', page: 5, heading: '', endPage: 5 },
      ],
    })
    mount(api, undefined, { plan })
    await vi.waitFor(() => expect(field()).toBeTruthy())
    expect(field().value).toBe('1-3, 5')
    expect(screen.queryByLabelText('Outline')).toBeNull()
    expect(splitButton().textContent?.trim()).toBe('Split into 2 PDFs')
    await new Promise((r) => setTimeout(r, 30))
    expect(api.save).not.toHaveBeenCalled()
  })

  it('a chapter job stays a chapter job without the query: nothing is saved and the source picker shows', async () => {
    const api = fakeApi()
    mount(api)
    await vi.waitFor(() => expect(splitButton()).toBeTruthy())
    expect(screen.getByLabelText('Outline')).toBeTruthy()
    expect(screen.queryByLabelText(/pages to keep/i)).toBeNull()
    expect(document.querySelector('.review')?.getAttribute('data-mode')).toBe('chapters')
    await new Promise((r) => setTimeout(r, 30))
    expect(api.save).not.toHaveBeenCalled()
  })
})
