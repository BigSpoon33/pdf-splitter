import { describe, expect, it } from 'vitest'
import { everyN, formatRanges, parseRanges, rangeName, rangeSections, spansOf } from './ranges'

describe('parseRanges', () => {
  it('reads ranges and single pages in any separator style (AC-4)', () => {
    expect(parseRanges('1-10, 15-20, 5-7', 30)).toEqual({
      ranges: [
        { page: 1, endPage: 10 },
        { page: 15, endPage: 20 },
        { page: 5, endPage: 7 },
      ],
      errors: [],
    })
    expect(parseRanges('1-10, 11-25, 40', 40).ranges).toEqual([
      { page: 1, endPage: 10 },
      { page: 11, endPage: 25 },
      { page: 40, endPage: 40 },
    ])
    expect(parseRanges(' 3 ;4–6\n7 — 8,,', 8).ranges).toEqual([
      { page: 3, endPage: 3 },
      { page: 4, endPage: 6 },
      { page: 7, endPage: 8 },
    ])
    expect(parseRanges('', 8)).toEqual({ ranges: [], errors: [] })
    expect(parseRanges('  ,  ', 8)).toEqual({ ranges: [], errors: [] })
  })

  it('reports each bad token and still parses the good ones', () => {
    const { ranges, errors } = parseRanges('1-10, abc, 10-5, 0, 31, 25-40, 2-3-4, -5, 20', 30)
    expect(ranges).toEqual([
      { page: 1, endPage: 10 },
      { page: 20, endPage: 20 },
    ])
    expect(errors).toEqual([
      { token: 'abc', message: 'Expected a page (40) or a range (1-10)' },
      { token: '10-5', message: '"10-5" is reversed: the first page must come first' },
      { token: '0', message: 'Pages start at 1' },
      { token: '31', message: 'Page 31 is past the last page (30)' },
      { token: '25-40', message: 'Page 40 is past the last page (30)' },
      { token: '2-3-4', message: 'Expected a page (40) or a range (1-10)' },
      { token: '-5', message: 'Expected a page (40) or a range (1-10)' },
    ])
  })

  it('bounds by the page count only when it is known', () => {
    expect(parseRanges('1-999').errors).toEqual([])
    expect(parseRanges('0-3').errors).toEqual([{ token: '0-3', message: 'Pages start at 1' }])
  })
})

describe('everyN', () => {
  it('fills whole spans and clips the last one (PRD AC-14: every 10 of 30 → 3)', () => {
    expect(everyN(10, 30)).toEqual([
      { page: 1, endPage: 10 },
      { page: 11, endPage: 20 },
      { page: 21, endPage: 30 },
    ])
    expect(everyN(7, 30).map((r) => [r.page, r.endPage])).toEqual([
      [1, 7],
      [8, 14],
      [15, 21],
      [22, 28],
      [29, 30],
    ])
    expect(everyN(1, 3)).toHaveLength(3)
    expect(everyN(50, 30)).toEqual([{ page: 1, endPage: 30 }])
  })

  it('gives nothing for a bad N or page count', () => {
    expect(everyN(0, 30)).toEqual([])
    expect(everyN(2.5, 30)).toEqual([])
    expect(everyN(NaN, 30)).toEqual([])
    expect(everyN(3, 0)).toEqual([])
  })
})

describe('formatRanges / rangeName / rangeSections', () => {
  it('round-trips through the text field and names spans with an en dash', () => {
    const ranges = parseRanges('1-10, 40, 15-20', 40).ranges
    expect(formatRanges(ranges)).toBe('1-10, 40, 15-20')
    expect(parseRanges(formatRanges(ranges), 40).ranges).toEqual(ranges)
    expect(rangeName({ page: 1, endPage: 10 })).toBe('Pages 1–10')
    expect(rangeName({ page: 40, endPage: 40 })).toBe('Page 40')
  })

  it('builds ranges sections with default names and keeps a rename of an unchanged span', () => {
    const first = rangeSections(parseRanges('1-10, 15-20', 30).ranges)
    expect(first).toEqual([
      { name: 'Pages 1–10', page: 1, heading: '', endPage: 10 },
      { name: 'Pages 15–20', page: 15, heading: '', endPage: 20 },
    ])
    const renamed = [{ ...first[0]!, name: 'Intro' }, first[1]!]
    // The same spans, one more at the end: the rename stays; a changed span gets the default name again.
    expect(rangeSections(parseRanges('1-10, 15-21, 25', 30).ranges, renamed).map((s) => s.name)).toEqual(['Intro', 'Pages 15–21', 'Page 25'])
    expect(rangeSections(parseRanges('2-10, 15-20', 30).ranges, renamed).map((s) => s.name)).toEqual(['Pages 2–10', 'Pages 15–20'])
  })

  it('spansOf tells lists apart by their spans only', () => {
    const a = rangeSections(parseRanges('1-10, 40', 40).ranges)
    expect(spansOf(a)).toBe('1-10,40-40')
    expect(spansOf([{ ...a[0]!, name: 'x' }, a[1]!])).toBe(spansOf(a))
    expect(spansOf([{ page: 3 }])).toBe('3-3')
  })
})
