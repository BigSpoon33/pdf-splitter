import { describe, expect, it } from 'vitest'
import { formatBytes, timeLeft } from './expiry'

const NOW = Date.parse('2026-09-25T12:00:00Z')
const at = (ms: number) => new Date(NOW + ms).toISOString()
const MIN = 60_000

describe('timeLeft', () => {
  it.each([
    [24 * 60 * MIN - 1, '23 h'],
    [60 * MIN, '1 h'],
    [59 * MIN + 59_000, '59 min'],
    [MIN, '1 min'],
    [59_000, 'less than a minute'],
  ])('%i ms → %s', (ms, text) => {
    expect(timeLeft(at(ms), NOW)).toBe(text)
  })

  it('is null once the time has passed, or for a value that is not a date', () => {
    expect(timeLeft(at(0), NOW)).toBeNull()
    expect(timeLeft(at(-MIN), NOW)).toBeNull()
    expect(timeLeft('soon', NOW)).toBeNull()
  })

  it('reads the API offset form (`+00:00`)', () => {
    expect(timeLeft('2026-09-26T11:30:00+00:00', NOW)).toBe('23 h')
  })
})

describe('formatBytes', () => {
  it.each([
    [900, '900 B'],
    [2048, '2 KB'],
    [56 * 1024 * 1024, '56.0 MB'],
  ])('%i → %s', (bytes, text) => {
    expect(formatBytes(bytes)).toBe(text)
  })
})
