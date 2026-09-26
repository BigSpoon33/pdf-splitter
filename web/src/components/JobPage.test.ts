import { fireEvent, render, screen } from '@testing-library/svelte'
import { describe, expect, it, vi } from 'vitest'
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
      phases = [
        row,
        { ...row, state: 'running', progress: 1, total: 3, message: 'Cutting sections' },
        { ...row, state: 'done', progress: 3, total: 3, message: null },
      ]
      manifest = ROWS
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

function mount(api: ReturnType<typeof fakeApi>, confirmDelete = vi.fn(() => true)) {
  return render(JobPage, {
    id: ID,
    load: api.load,
    pollMs: 5,
    review: {
      loadAnalysis: async () => analysisOf(),
      loadPlan: async () => planOf(),
      loadManifest: api.loadManifest,
      loadSectionPlan: async () => Promise.reject(new ApiError(500, 'preview_failed')),
      loadSheet: async () => new Blob(),
      save: api.save,
      cut: api.cut,
      debounceMs: 10,
    },
    expiry: { remove: api.remove, confirmDelete },
  })
}

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

  it('an expires_at already past is the deleted screen (AC-4)', async () => {
    const api = fakeApi({ expires_at: new Date(Date.now() - 1000).toISOString() })
    mount(api)
    await vi.waitFor(() => expect(goneScreen()).toBe('expired'))
  })
})
