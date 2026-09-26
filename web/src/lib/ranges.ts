/**
 * Page-range mode (ADR-009, STORY-015): the text a visitor types (`1-10, 15-20, 40`) → whole-page spans, the
 * "every N pages" helper, and the sections a `ranges` plan carries. Like `plan.ts:parseList`, every bad token gets
 * its own error and the good ones still parse, so the visitor fixes them in place. Spans may overlap or leave gaps:
 * the API copies each one on its own (`tests/test_ranges.py::test_cut_ranges_copies_each_span_with_its_pages`).
 */
import type { Section } from './api'
import { MAX_NAME } from './plan'

export interface PageRange {
  /** 1-based sheets (ADR-003), both inclusive. */
  page: number
  endPage: number
}

export interface TokenError {
  /** The token as typed, trimmed. */
  token: string
  message: string
}

export interface ParsedRanges {
  ranges: PageRange[]
  errors: TokenError[]
}

const INTEGER = /^\d+$/
// `1-10`, `1 – 10` (an en dash, since the default names use one and get pasted back), `1—10`.
const SPAN = /^(\d+)\s*[-–—]\s*(\d+)$/

/** Tokens are separated by commas, semicolons or line breaks; blank tokens are skipped. */
export function parseRanges(text: string, pages?: number): ParsedRanges {
  const ranges: PageRange[] = []
  const errors: TokenError[] = []
  for (const raw of text.split(/[,;\n]/)) {
    const token = raw.trim()
    if (!token) continue
    const fail = (message: string) => errors.push({ token, message })
    let page: number
    let endPage: number
    if (INTEGER.test(token)) {
      page = endPage = Number(token)
    } else {
      const m = SPAN.exec(token)
      if (!m) {
        fail('Expected a page (40) or a range (1-10)')
        continue
      }
      page = Number(m[1])
      endPage = Number(m[2])
    }
    if (page < 1 || endPage < 1) {
      fail('Pages start at 1')
      continue
    }
    if (endPage < page) {
      fail(`"${token}" is reversed: the first page must come first`)
      continue
    }
    if (pages !== undefined && endPage > pages) {
      fail(`Page ${endPage} is past the last page (${pages})`)
      continue
    }
    ranges.push({ page, endPage })
  }
  return { ranges, errors }
}

/** `[1, n], [n+1, 2n], …`, the last one clipped at `pages`: 30 pages by 10 → three; by 7 → 1-7, 8-14, 15-21, 22-28, 29-30. */
export function everyN(n: number, pages: number): PageRange[] {
  if (!Number.isInteger(n) || n < 1 || !Number.isInteger(pages) || pages < 1) return []
  const out: PageRange[] = []
  for (let page = 1; page <= pages; page += n) out.push({ page, endPage: Math.min(page + n - 1, pages) })
  return out
}

/** The inverse of `parseRanges`, for the text field: `1-10, 15-20, 40`. */
export function formatRanges(ranges: PageRange[]): string {
  return ranges.map((r) => (r.page === r.endPage ? String(r.page) : `${r.page}-${r.endPage}`)).join(', ')
}

/** The default display name (an en dash, as a range reads in print): `Pages 1–10`, `Page 40`. */
export function rangeName({ page, endPage }: PageRange): string {
  return page === endPage ? `Page ${page}` : `Pages ${page}–${endPage}`
}

/**
 * The sections for `ranges`: a range keeps the name of the section at the same position when that section covers
 * the same span (a rename survives the text being edited around it); any other gets the default name.
 */
export function rangeSections(ranges: PageRange[], previous: Section[] = []): Section[] {
  return ranges.map((r, i) => {
    const was = previous[i]
    const keep = was !== undefined && was.page === r.page && was.endPage === r.endPage
    return { name: (keep ? was.name : rangeName(r)).slice(0, MAX_NAME), page: r.page, heading: '', endPage: r.endPage }
  })
}

/** The spans of a section list, for telling one list from another regardless of names: `1-10,15-20`. */
export function spansOf(sections: readonly Pick<Section, 'page' | 'endPage'>[]): string {
  return sections.map((s) => `${s.page}-${s.endPage ?? s.page}`).join(',')
}
