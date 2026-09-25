import { describe, expect, it } from 'vitest'
import { jobPath, parseRoute } from './route'

describe('parseRoute', () => {
  it.each([
    ['/', { name: 'home' }],
    ['/j/AbC-_12345678901234567', { name: 'job', id: 'AbC-_12345678901234567' }],
    ['/j/AbC-_12345678901234567/', { name: 'job', id: 'AbC-_12345678901234567' }],
    ['/j/', { name: 'not_found' }],
    ['/j/a%2Fb', { name: 'not_found' }],
    ['/j/abc/extra', { name: 'not_found' }],
    ['/elsewhere', { name: 'not_found' }],
  ])('%s', (path, route) => {
    expect(parseRoute(path)).toEqual(route)
  })

  it('round-trips a job id', () => {
    expect(parseRoute(jobPath('Zz9_-x'))).toEqual({ name: 'job', id: 'Zz9_-x' })
  })
})
