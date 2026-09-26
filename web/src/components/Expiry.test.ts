import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../lib/api'
import { MESSAGES } from '../lib/errors'
import Expiry from './Expiry.svelte'

const ID = 'AbCdEfGhIjKlMnOpQrStUv'
const START = Date.parse('2026-09-25T12:00:00Z')
const MIN = 60_000

beforeEach(() => {
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
})

function mount(expiresInMs: number, over: { remove?: (id: string) => Promise<void>; confirmDelete?: () => boolean } = {}) {
  let clock = START
  const props = {
    id: ID,
    expiresAt: new Date(START + expiresInMs).toISOString(),
    ondeleted: vi.fn(),
    onexpired: vi.fn(),
    remove: vi.fn(over.remove ?? (async () => {})),
    confirmDelete: vi.fn(over.confirmDelete ?? (() => true)),
    now: () => clock,
  }
  render(Expiry, props)
  const advance = async (ms: number) => {
    clock += ms
    await vi.advanceTimersByTimeAsync(ms)
  }
  return { ...props, advance }
}

describe('Expiry', () => {
  it('counts down once a minute and reports the moment the time runs out (AC-3, AC-4)', async () => {
    const { onexpired, advance } = mount(60 * MIN + 30_000)
    await vi.advanceTimersByTimeAsync(0)
    expect(screen.getByText('Files deleted in 1 h.')).toBeTruthy()
    await advance(MIN)
    expect(screen.getByText('Files deleted in 59 min.')).toBeTruthy()
    expect(onexpired).not.toHaveBeenCalled()
    await advance(60 * MIN)
    expect(onexpired).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: 'Delete now' })).toBeNull()
  })

  it('a job already gone when deleting counts as deleted', async () => {
    const gone = mount(23 * 60 * MIN, { remove: async () => Promise.reject(new ApiError(410, 'expired')) })
    await fireEvent.click(screen.getByRole('button', { name: 'Delete now' }))
    await vi.advanceTimersByTimeAsync(0)
    expect(gone.ondeleted).toHaveBeenCalledTimes(1)
  })

  it('any other delete failure is shown in place and the page stays', async () => {
    const failing = mount(23 * 60 * MIN, { remove: async () => Promise.reject(new ApiError(0, 'network')) })
    await fireEvent.click(screen.getByRole('button', { name: 'Delete now' }))
    await vi.advanceTimersByTimeAsync(0)
    expect(failing.ondeleted).not.toHaveBeenCalled()
    expect(screen.getByRole('alert').textContent).toBe(MESSAGES.network)
  })
})
