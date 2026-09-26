/**
 * Test fixtures only (imported by `*.test.ts`, never by the app). The analysis mirrors
 * `tests/fixtures/books.py::headed_book` as `tests/test_worker.py::test_analyze_outline_book` pins it (6 sheets,
 * outline levels [3, 3], 16 pt chapters), with a second heading level and page labels added so the level
 * select and the printed-label hint have something to show.
 */
import type { Analysis, ManifestRow, Plan, Section } from './api'

const CHAPTERS: [string, number][] = [
  ['Foundations of Testing', 1],
  ['Chapter Two: The Middle of the Synthetic Book', 3],
  ['Closing Chapter', 4],
]
const SECTIONS: [string, number][] = [
  ['First Principles', 1],
  ['Second Principles of Wrapped Section Headings', 2],
  ['Middle Matters', 3],
  ['Final Section', 5],
]

export function analysisOf(over: Partial<Analysis> = {}): Analysis {
  return {
    pages: 6,
    pageLabels: ['', '', '', '', '', ''],
    size: Array.from({ length: 6 }, () => ({ W: 522.7, H: 789.6 })),
    outline: {
      levels: [3, 3],
      items: [
        ...CHAPTERS.map(([heading, page], i) => ({ name: `${i + 1} ${heading}`, page, heading, level: 1 })),
        ...SECTIONS.slice(0, 3).map(([heading, page], i) => ({ name: `${i + 1}.1 ${heading}`, page, heading, level: 2 })),
      ],
    },
    headings: {
      body_size: 9.5,
      levels: [
        { size: 16, count: 3 },
        { size: 12, count: 4 },
      ],
      candidates: [
        ...CHAPTERS.map(([name, page]) => ({ name, page, heading: name, size: 16, level: 1, y: 90, col: 'left' as const })),
        ...SECTIONS.map(([name, page]) => ({ name, page, heading: name, size: 12, level: 2, y: 300, col: 'left' as const })),
      ],
    },
    suggested: { source: 'outline', level: 1 },
    ...over,
  }
}

export function sectionsOf(pairs: [string, number][]): Section[] {
  return pairs.map(([name, page]) => ({ name, page, heading: name }))
}

/** The default plan of `analysisOf()`: outline level 1, default settings, no overrides. */
export function planOf(over: Partial<Plan> = {}): Plan {
  return {
    source: 'outline',
    settings: { column_split: 0.487, single_column: false, header_band: 50, footer_band: 32, heading_min_size: 12.5 },
    sections: sectionsOf(CHAPTERS.map(([h, p], i) => [`${i + 1} ${h}`, p])),
    overrides: {},
    ...over,
  }
}

export function rowOf(index: number, name: string, flags: string[] = []): ManifestRow {
  return { index, name, file: `${String(index + 1).padStart(3, '0')} - ${name}.pdf`, flags, notes: [], leaks: [], bytes: 1000 }
}

/**
 * A plan past the keepalive budget (`KEEPALIVE_MAX_BYTES`): Maciocia "Headings → any level" is ~1,650 sections
 * and ~142 KB of JSON, the case that made a keepalive PUT refused (gate r2).
 */
export function bigPlanOf(count = 1700): Plan {
  return planOf({
    source: 'headings',
    sections: sectionsOf(
      Array.from({ length: count }, (_, i) => [`${i + 1} A heading long enough to make the plan heavy`, (i % 6) + 1]),
    ),
  })
}
