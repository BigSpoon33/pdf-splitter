import { afterEach, describe, expect, it, vi } from 'vitest'
import { jobPath, linkClick, onNavigate, parseRoute } from './route'

describe('parseRoute', () => {
  it.each([
    ['/', { name: 'home' }],
    ['/j/AbC-_12345678901234567', { name: 'job', id: 'AbC-_12345678901234567' }],
    ['/j/AbC-_12345678901234567/', { name: 'job', id: 'AbC-_12345678901234567' }],
    ['/j/', { name: 'not_found' }],
    ['/j/a%2Fb', { name: 'not_found' }],
    ['/j/abc/extra', { name: 'not_found' }],
    ['/privacy', { name: 'privacy' }],
    ['/terms/', { name: 'terms' }],
    ['/privacy/extra', { name: 'not_found' }],
    ['/elsewhere', { name: 'not_found' }],
  ])('%s', (path, route) => {
    expect(parseRoute(path)).toEqual(route)
  })

  it('round-trips a job id', () => {
    expect(parseRoute(jobPath('Zz9_-x'))).toEqual({ name: 'job', id: 'Zz9_-x' })
  })
})

describe('linkClick', () => {
  afterEach(() => history.replaceState(null, '', '/'))

  it('navigates in place on a plain click and leaves a modified click to the browser', () => {
    const seen = vi.fn()
    const off = onNavigate(seen)
    const plain = new MouseEvent('click', { button: 0, cancelable: true })
    linkClick(plain, '/terms')
    expect(plain.defaultPrevented).toBe(true)
    expect(location.pathname).toBe('/terms')
    expect(seen).toHaveBeenCalledWith('/terms')

    const modified = new MouseEvent('click', { button: 0, ctrlKey: true, cancelable: true })
    linkClick(modified, '/privacy')
    expect(modified.defaultPrevented).toBe(false)
    expect(location.pathname).toBe('/terms')
    off()
  })
})
