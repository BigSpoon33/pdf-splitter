import { render, screen } from '@testing-library/svelte'
import { tick } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, type JobStatus as Status } from '../lib/api'
import { MESSAGES } from '../lib/errors'
import JobStatus from './JobStatus.svelte'

const ID = 'AbCdEfGhIjKlMnOpQrStUv'

function status(over: Partial<Status>): Status {
  return {
    id: ID,
    state: 'queued',
    kind: 'analyze',
    progress: 0,
    total: 0,
    queue_position: null,
    message: null,
    error_code: null,
    expires_at: '2026-09-26T12:00:00+00:00',
    filename: 'My Book.pdf',
    pages: 6,
    ...over,
  }
}

/** A loader that answers each poll with the next item (an Error is thrown). */
function sequence(...answers: (Status | Error)[]) {
  return vi.fn(async () => {
    const next = answers.length > 1 ? answers.shift()! : answers[0]!
    if (next instanceof Error) throw next
    return next
  })
}

// vi.waitFor advances fake timers itself, so the timing test settles promises explicitly instead.
async function settle() {
  await vi.advanceTimersByTimeAsync(0)
  await tick()
}

const stateShown = () => document.querySelector('[data-state]')?.getAttribute('data-state')

beforeEach(() => {
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
})

describe('JobStatus', () => {
  it('polls every 1.5 s through queued → running → review, then stops', async () => {
    const load = sequence(
      status({ state: 'queued' }),
      status({ state: 'running', progress: 2, total: 6, message: 'Indexing pages' }),
      status({ state: 'review', progress: 6, total: 6 }),
    )
    render(JobStatus, { id: ID, load })
    await settle()
    expect(stateShown()).toBe('queued')
    expect(load).toHaveBeenCalledWith(ID, expect.any(AbortSignal))
    expect(screen.queryByText(/position in queue/i)).toBeNull()

    await vi.advanceTimersByTimeAsync(1499)
    expect(load).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    await settle()
    expect(load).toHaveBeenCalledTimes(2)
    expect(stateShown()).toBe('running')
    expect(screen.getByText('Indexing pages')).toBeTruthy()
    expect(screen.getByText('2 / 6')).toBeTruthy()
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('2')

    await vi.advanceTimersByTimeAsync(1500)
    await settle()
    expect(stateShown()).toBe('review')
    await vi.advanceTimersByTimeAsync(10_000)
    expect(load).toHaveBeenCalledTimes(3)
  })

  it('shows the queue position once the API reports one', async () => {
    render(JobStatus, { id: ID, load: sequence(status({ state: 'queued', queue_position: 3 })) })
    await vi.waitFor(() => expect(screen.getByText('Position in queue: 3')).toBeTruthy())
  })

  it("shows a failed job's own message and stops", async () => {
    const load = sequence(
      status({ state: 'failed', error_code: 'resources', message: 'The PDF needed more memory.' }),
    )
    render(JobStatus, { id: ID, load })
    await vi.waitFor(() => expect(screen.getByRole('alert').textContent).toBe('The PDF needed more memory.'))
    await vi.advanceTimersByTimeAsync(10_000)
    expect(load).toHaveBeenCalledTimes(1)
  })

  it.each([
    [410, 'expired'],
    [404, 'not_found'],
  ] as const)('stops on %i %s and says why', async (httpStatus, code) => {
    const load = sequence(status({ state: 'running' }), new ApiError(httpStatus, code))
    render(JobStatus, { id: ID, load })
    await vi.advanceTimersByTimeAsync(1500)
    await vi.waitFor(() => expect(screen.getByRole('alert').textContent).toBe(MESSAGES[code]))
    await vi.advanceTimersByTimeAsync(10_000)
    expect(load).toHaveBeenCalledTimes(2)
  })

  it('keeps polling through a transient error', async () => {
    const load = sequence(
      status({ state: 'running' }),
      new ApiError(0, 'network'),
      status({ state: 'review' }),
    )
    render(JobStatus, { id: ID, load })
    await vi.advanceTimersByTimeAsync(1500)
    await vi.waitFor(() => expect(screen.getByRole('status').textContent).toContain(MESSAGES.network))
    await vi.advanceTimersByTimeAsync(1500)
    await vi.waitFor(() => expect(stateShown()).toBe('review'))
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('shows a transient error before the first answer, then the job once a poll succeeds', async () => {
    const load = sequence(
      new ApiError(0, 'network'),
      new ApiError(500, 'internal'),
      status({ state: 'running', progress: 1, total: 6 }),
    )
    render(JobStatus, { id: ID, load })
    await vi.waitFor(() => expect(screen.getByRole('status').textContent).toBe(`${MESSAGES.network} Retrying…`))
    expect(screen.queryByText('Loading…')).toBeNull()
    await vi.advanceTimersByTimeAsync(1500)
    await vi.waitFor(() => expect(screen.getByRole('status').textContent).toBe(`${MESSAGES.internal} Retrying…`))
    await vi.advanceTimersByTimeAsync(1500)
    await vi.waitFor(() => expect(stateShown()).toBe('running'))
    expect(screen.queryByRole('status')).toBeNull()
    expect(load).toHaveBeenCalledTimes(3)
  })

  it('clears aria-busy once polling stops on a fatal error', async () => {
    const load = sequence(status({ state: 'running' }), new ApiError(410, 'expired'))
    const { container } = render(JobStatus, { id: ID, load })
    const card = () => container.querySelector('section')!.getAttribute('aria-busy')
    await vi.waitFor(() => expect(stateShown()).toBe('running'))
    expect(card()).toBe('true')
    await vi.advanceTimersByTimeAsync(1500)
    await vi.waitFor(() => expect(screen.getByRole('alert').textContent).toBe(MESSAGES.expired))
    expect(card()).toBe('false')
  })

  it('clears aria-busy on a terminal state', async () => {
    const { container } = render(JobStatus, { id: ID, load: sequence(status({ state: 'review' })) })
    await vi.waitFor(() => expect(stateShown()).toBe('review'))
    expect(container.querySelector('section')!.getAttribute('aria-busy')).toBe('false')
  })

  it('stops polling when unmounted', async () => {
    const load = sequence(status({ state: 'running' }))
    const { unmount } = render(JobStatus, { id: ID, load })
    await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(1))
    unmount()
    await vi.advanceTimersByTimeAsync(10_000)
    expect(load).toHaveBeenCalledTimes(1)
  })
})
