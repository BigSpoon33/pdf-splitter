import { describe, expect, it } from 'vitest'
import { analysisOf, planOf, rowOf, sectionsOf } from './fixtures'
import {
  badgeFlags,
  flagLabel,
  formatList,
  headingSections,
  initialHeadingLevel,
  initialOutlineLevel,
  insertSection,
  MAX_NAME,
  mergeWithNext,
  outlineSections,
  parseList,
  removeSection,
  rowFor,
  shiftOverrides,
  thresholdMax,
} from './plan'

describe('outlineSections', () => {
  it('takes the items at exactly one level, name/page/heading as default_plan does', () => {
    const a = analysisOf()
    expect(outlineSections(a, 1).map((s) => [s.name, s.page])).toEqual([
      ['1 Foundations of Testing', 1],
      ['2 Chapter Two: The Middle of the Synthetic Book', 3],
      ['3 Closing Chapter', 4],
    ])
    expect(outlineSections(a, 1)[0]?.heading).toBe('Foundations of Testing')
    expect(outlineSections(a, 2)).toHaveLength(3)
    expect(outlineSections(a, 3)).toEqual([])
  })

  it('caps names and headings at the Plan limits', () => {
    const a = analysisOf({ outline: { levels: [1], items: [{ name: 'n'.repeat(200), page: 1, heading: 'h'.repeat(600), level: 1 }] } })
    const [s] = outlineSections(a, 1)
    expect(s?.name).toHaveLength(MAX_NAME)
    expect(s?.heading).toHaveLength(500)
  })

  it('starts from the suggested level, else the first non-empty one', () => {
    expect(initialOutlineLevel(analysisOf())).toBe(1)
    expect(initialOutlineLevel(analysisOf({ suggested: { source: 'headings', level: 1 }, outline: { levels: [0, 5], items: [] } }))).toBe(2)
    expect(initialOutlineLevel(analysisOf({ suggested: { source: 'manual', level: null }, outline: { levels: [], items: [] } }))).toBe(1)
  })
})

describe('headingSections', () => {
  const a = analysisOf()

  it('filters by threshold × body size and by level', () => {
    expect(headingSections(a, 0, 1).map((s) => s.page)).toEqual([1, 3, 4, 1, 2, 3, 5])
    expect(headingSections(a, 0, 1.3).map((s) => s.name)).toEqual([
      'Foundations of Testing',
      'Chapter Two: The Middle of the Synthetic Book',
      'Closing Chapter',
    ])
    expect(headingSections(a, 2, 1)).toHaveLength(4)
    expect(headingSections(a, 2, 1.3)).toEqual([])
    expect(headingSections(a, 1, 1.68)).toHaveLength(3)
    expect(headingSections(a, 1, 1.69)).toEqual([])
  })

  it('slider top is the biggest level in body sizes, rounded up to 0.1, at least 2', () => {
    expect(thresholdMax(a)).toBe(2) // 16 / 9.5 = 1.68 → 1.7, but the floor of 2 wins
    expect(thresholdMax(analysisOf({ headings: { body_size: 10, levels: [{ size: 30, count: 1 }], candidates: [] } }))).toBe(3)
    expect(thresholdMax(analysisOf({ headings: { body_size: 10, levels: [{ size: 26.1, count: 1 }], candidates: [] } }))).toBe(2.7)
    expect(thresholdMax(analysisOf({ headings: { body_size: 0, levels: [], candidates: [] } }))).toBe(2)
  })

  it('starts from the suggested heading level, else level 1 (0 = any, when there are none)', () => {
    expect(initialHeadingLevel(a)).toBe(1)
    expect(initialHeadingLevel(analysisOf({ suggested: { source: 'headings', level: 2 } }))).toBe(2)
    expect(initialHeadingLevel(analysisOf({ headings: { body_size: 9, levels: [], candidates: [] } }))).toBe(0)
  })
})

describe('parseList', () => {
  it('parses `Name, page` lines, splitting on the last comma, skipping blanks', () => {
    const { sections, errors } = parseList('Intro, 1\n\n  Chapter 1, part 2 , 3 \nEnd\t5\n', 6)
    expect(errors).toEqual([])
    expect(sections).toEqual([
      { name: 'Intro', page: 1, heading: '' },
      { name: 'Chapter 1, part 2', page: 3, heading: '' },
      { name: 'End', page: 5, heading: '' },
    ])
  })

  it('reports each bad line with its number and keeps the good ones', () => {
    const text = ['Good, 1', 'No page here', ', 2', 'Zero, 0', 'Past, 7', 'Float, 1.5', `${'n'.repeat(121)}, 2`, 'Fine, 6'].join('\n')
    const { sections, errors } = parseList(text, 6)
    expect(sections.map((s) => s.name)).toEqual(['Good', 'Fine'])
    expect(errors.map((e) => [e.line, e.message])).toEqual([
      [2, 'Expected "Name, page"'],
      [3, 'The name is missing'],
      [4, 'Pages start at 1'],
      [5, 'Page 7 is past the last page (6)'],
      [6, '"1.5" is not a page number'],
      [7, 'The name is longer than 120 characters'],
    ])
    expect(errors[0]?.text).toBe('No page here')
  })

  it('does not bound the page when the book size is unknown', () => {
    expect(parseList('A, 900').errors).toEqual([])
  })

  it('round-trips through formatList', () => {
    const sections = sectionsOf([['One, two', 1], ['Three', 4]])
    expect(parseList(formatList(sections), 6).sections.map((s) => [s.name, s.page])).toEqual([
      ['One, two', 1],
      ['Three', 4],
    ])
  })
})

describe('list operations re-key overrides by section index', () => {
  const plan = planOf({
    sections: sectionsOf([['A', 1], ['B', 2], ['C', 3], ['D', 4]]),
    overrides: {
      '0': { startCut: 10 },
      '1': { startCut: 20, endCut: 25 },
      '2': { endCut: 30, endCol: 'left' },
      '3': { startCol: 'right' },
    },
  })

  it('shiftOverrides maps keys and drops the gone', () => {
    expect(shiftOverrides(plan.overrides, (i) => (i === 1 ? null : i + 1))).toEqual({
      '1': { startCut: 10 },
      '3': { endCut: 30, endCol: 'left' },
      '4': { startCol: 'right' },
    })
  })

  it('removeSection drops the section and its override and shifts the later ones down', () => {
    const next = removeSection(plan, 1)
    expect(next.sections.map((s) => s.name)).toEqual(['A', 'C', 'D'])
    expect(next.overrides).toEqual({ '0': { startCut: 10 }, '1': { endCut: 30, endCol: 'left' }, '2': { startCol: 'right' } })
    expect(plan.sections).toHaveLength(4) // the input is untouched
  })

  it('mergeWithNext keeps the first start, takes the next end, shifts the rest', () => {
    const next = mergeWithNext(plan, 1)
    expect(next.sections.map((s) => s.name)).toEqual(['A', 'B', 'D'])
    expect(next.overrides).toEqual({
      '0': { startCut: 10 },
      '1': { startCut: 20, endCut: 30, endCol: 'left' },
      '2': { startCol: 'right' },
    })
  })

  it('mergeWithNext leaves no empty override behind and is a no-op on the last section', () => {
    const bare = planOf({ sections: sectionsOf([['A', 1], ['B', 2]]) })
    expect(mergeWithNext(bare, 0).overrides).toEqual({})
    expect(mergeWithNext(bare, 0).sections.map((s) => s.name)).toEqual(['A'])
    expect(mergeWithNext(bare, 1)).toBe(bare)
  })

  it('insertSection keeps book order and shifts overrides from the slot up', () => {
    const { plan: next, index } = insertSection(plan, { name: 'B2', page: 2, heading: '' })
    expect(index).toBe(2)
    expect(next.sections.map((s) => s.name)).toEqual(['A', 'B', 'B2', 'C', 'D'])
    expect(next.overrides).toEqual({
      '0': { startCut: 10 },
      '1': { startCut: 20, endCut: 25 },
      '3': { endCut: 30, endCol: 'left' },
      '4': { startCol: 'right' },
    })
    expect(insertSection(plan, { name: 'Z', page: 9, heading: '' }).index).toBe(4)
    expect(insertSection(plan, { name: 'Front', page: 1, heading: '' }).index).toBe(1)
  })
})

describe('flags', () => {
  it('drops span-clamped on the last section only', () => {
    const row = rowOf(0, 'A', ['span-clamped', 'heading-not-found'])
    expect(badgeFlags(row, true)).toEqual(['heading-not-found'])
    expect(badgeFlags(row, false)).toEqual(['span-clamped', 'heading-not-found'])
    expect(badgeFlags(undefined, false)).toEqual([])
  })

  it('labels known flags and passes unknown ones through', () => {
    expect(flagLabel('heading-not-found')).toBe('Heading not found on its page')
    expect(flagLabel('something-new')).toBe('something-new')
  })

  it('rowFor matches a row only while the section at that index still has its name', () => {
    const plan = planOf({ sections: sectionsOf([['A', 1], ['B', 3]]) })
    const rows = [rowOf(0, 'A', ['leak']), rowOf(1, 'B')]
    expect(rowFor(rows, plan, 0)?.flags).toEqual(['leak'])
    expect(rowFor(rows, removeSection(plan, 0), 0)).toBeUndefined()
    expect(rowFor(rows, { ...plan, sections: sectionsOf([['A renamed', 1], ['B', 3]]) }, 0)).toBeUndefined()
    expect(rowFor(rows, plan, 5)).toBeUndefined()
  })
})
