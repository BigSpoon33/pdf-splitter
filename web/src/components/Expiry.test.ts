import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../lib/api'
import { MESSAGES } from '../lib/errors'
import Expiry from './Expiry.svelte'

const ID = 'AbCdEfGhIjKlMnOpQrStUv'
const MIN = 60_000
const HOUR = 60 * MIN

beforeEach(() => {
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
})

/** The monotonic clock the component reads; the fake timers and this counter advance together. */
function mount(secondsLeft: number, over: { remove?: (id: string) => Promise<void>; confirmDelete?: () => boolean } = {}) {
  let clock = 5_000
  const props = {
    id: ID,
    secondsLeft,
    receivedAt: clock,
    ondeleted: vi.fn(),
    onexpired: vi.fn(),
    remove: vi.fn(over.remove ?? (async () => {})),
    confirmDelete: vi.fn(over.confirmDelete ?? (() => true)),
    now: () => clock,
    graceMs: 1000,
  }
  const { rerender, unmount } = render(Expiry, props)
  const advance = async (ms: number) => {
    clock += ms
    await vi.advanceTimersByTimeAsync(ms)
  }
  /** A later status: the server's fresh count, anchored at the moment it arrives. */
  const status = async (seconds: number) => {
    await rerender({ secondsLeft: seconds, receivedAt: clock })
  }
  return { ...props, advance, status, unmount }
}

const line = () => screen.getByText(/^Files deleted in/).textContent

describe('Expiry', () => {
  it("counts down from the server's seconds_left once a minute, and asks the page to poll once the count runs out (AC-3, gate r1)", async () => {
    const { onexpired, advance } = mount(60 * 60 + 30)
    await vi.advanceTimersByTimeAsync(0)
    expect(line()).toBe('Files deleted in 1 h.')
    await advance(MIN)
    expect(line()).toBe('Files deleted in 59 min.')
    await advance(59 * MIN)
    expect(line()).toBe('Files deleted in less than a minute.')
    expect(onexpired).not.toHaveBeenCalled()
    // The count runs out at +30 s; the grace lets the server's whole-second deadline pass before it is asked.
    await advance(30_000 + 999)
    expect(onexpired).not.toHaveBeenCalled()
    await advance(1)
    expect(onexpired).toHaveBeenCalledTimes(1)
    // Nothing here declares the job gone: the line and the button stay until the API answers 410.
    expect(line()).toBe('Files deleted in less than a minute.')
    expect(screen.getByRole('button', { name: 'Delete now' })).toBeTruthy()
    await advance(10 * MIN)
    expect(onexpired).toHaveBeenCalledTimes(1)
  })

  it("a fresh status re-arms the countdown from the server's new count (a clock that was off, or a job kept alive)", async () => {
    const { onexpired, advance, status } = mount(0)
    await advance(1000)
    expect(onexpired).toHaveBeenCalledTimes(1)
    await status(2 * 60 * 60)
    expect(line()).toBe('Files deleted in 2 h.')
    await advance(HOUR)
    expect(line()).toBe('Files deleted in 1 h.')
    expect(onexpired).toHaveBeenCalledTimes(1)
  })

  it("ignores the visitor's wall clock entirely: Date set 25 h ahead or behind changes nothing (gate r1 F1)", async () => {
    for (const skew of [25 * HOUR, -25 * HOUR]) {
      vi.setSystemTime(Date.now() + skew)
      const { onexpired, advance, unmount } = mount(23.5 * 60 * 60)
      await advance(MIN)
      expect(line()).toBe('Files deleted in 23 h.')
      expect(onexpired).not.toHaveBeenCalled()
      unmount()
    }
  })

  it('a job already gone when deleting counts as deleted', async () => {
    const gone = mount(23 * 60 * 60, { remove: async () => Promise.reject(new ApiError(410, 'expired')) })
    await fireEvent.click(screen.getByRole('button', { name: 'Delete now' }))
    await vi.advanceTimersByTimeAsync(0)
    expect(gone.ondeleted).toHaveBeenCalledTimes(1)
  })

  it('any other delete failure is shown in place and the page stays', async () => {
    const failing = mount(23 * 60 * 60, { remove: async () => Promise.reject(new ApiError(0, 'network')) })
    await fireEvent.click(screen.getByRole('button', { name: 'Delete now' }))
    await vi.advanceTimersByTimeAsync(0)
    expect(failing.ondeleted).not.toHaveBeenCalled()
    expect(screen.getByRole('alert').textContent).toBe(MESSAGES.network)
  })
})
