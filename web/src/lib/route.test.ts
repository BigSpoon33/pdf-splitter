import { afterEach, describe, expect, it, vi } from 'vitest'
import { href, jobPath, linkClick, navigate, onNavigate, parseRoute } from './route'

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
    // ADR-009: the mode rides the query; anything else there is ignored, and a query never rescues a bad path.
    ['/j/AbC-_12345678901234567?mode=ranges', { name: 'job', id: 'AbC-_12345678901234567', mode: 'ranges' }],
    ['/j/AbC-_12345678901234567/?mode=ranges&x=1', { name: 'job', id: 'AbC-_12345678901234567', mode: 'ranges' }],
    ['/j/AbC-_12345678901234567?mode=chapters', { name: 'job', id: 'AbC-_12345678901234567' }],
    ['/j/AbC-_12345678901234567?other=1', { name: 'job', id: 'AbC-_12345678901234567' }],
    ['/?mode=ranges', { name: 'home' }],
    ['/j/?mode=ranges', { name: 'not_found' }],
  ])('%s', (path, route) => {
    expect(parseRoute(path)).toEqual(route)
  })

  it('round-trips a job id, with and without the ranges mode', () => {
    expect(parseRoute(jobPath('Zz9_-x'))).toEqual({ name: 'job', id: 'Zz9_-x' })
    expect(jobPath('Zz9_-x', 'ranges')).toBe('/j/Zz9_-x?mode=ranges')
    expect(parseRoute(jobPath('Zz9_-x', 'ranges'))).toEqual({ name: 'job', id: 'Zz9_-x', mode: 'ranges' })
  })
})

describe('navigate', () => {
  afterEach(() => history.replaceState(null, '', '/'))

  it('keeps the query in the URL and reports it, so a reload lands on the same mode (AC-1)', () => {
    const seen = vi.fn()
    const off = onNavigate(seen)
    navigate('/j/Zz9_-x?mode=ranges')
    expect(location.pathname + location.search).toBe('/j/Zz9_-x?mode=ranges')
    expect(seen).toHaveBeenCalledWith('/j/Zz9_-x?mode=ranges')
    expect(href()).toBe('/j/Zz9_-x?mode=ranges')
    // The same page without the query is a different place: the mode came off.
    navigate('/j/Zz9_-x')
    expect(seen).toHaveBeenLastCalledWith('/j/Zz9_-x')
    off()
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
