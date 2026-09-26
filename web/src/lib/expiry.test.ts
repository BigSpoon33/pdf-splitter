import { describe, expect, it } from 'vitest'
import { formatBytes, timeLeft } from './expiry'

const MIN = 60_000

describe('timeLeft', () => {
  it.each([
    [24 * 60 * MIN - 1, '23 h'],
    [60 * MIN, '1 h'],
    [59 * MIN + 59_000, '59 min'],
    [MIN, '1 min'],
    [59_000, 'less than a minute'],
  ])('%i ms → %s', (ms, text) => {
    expect(timeLeft(ms)).toBe(text)
  })

  it('never claims the files are gone: at and past zero it is the server\'s 410 that says so', () => {
    expect(timeLeft(0)).toBe('less than a minute')
    expect(timeLeft(-MIN)).toBe('less than a minute')
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
