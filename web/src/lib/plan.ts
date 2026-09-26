/**
 * Pure edits to a Plan: building section lists from the analysis (AC-1), the paste-list parser, and the
 * list operations that shift section indexes (AC-3). Overrides are keyed by section INDEX, so every
 * insert/delete/merge re-keys them here — the API refuses a key past the end
 * (`tests/test_api_e2e.py::test_put_plan_rejects_bad_plans_with_field_errors`).
 * Limits mirror `src/pdf_splitter/models.py` (`Section`, `PlanSettings`).
 */
import type { Analysis, ManifestRow, Override, Plan, PlanSettings, Section } from './api'

export const MAX_NAME = 120
export const MAX_HEADING = 500
export const MIN_THRESHOLD = 1
/** The analysis detects candidates up to this many characters (`heading_candidates(max_len=90)` in the engine). */
export const MAX_HEADING_LENGTH = 90

// ── Sources ──────────────────────────────────────────────────────────────────────────────────────────

function section(name: string, page: number, heading: string): Section {
  return { name: name.slice(0, MAX_NAME), page, heading: heading.slice(0, MAX_HEADING) }
}

/** The outline items at exactly `level` (1-based), as the worker's `default_plan` builds them. */
export function outlineSections(analysis: Analysis, level: number): Section[] {
  return analysis.outline.items.filter((it) => it.level === level).map((it) => section(it.name, it.page, it.heading))
}

/**
 * What the picker's controls hold. It lives in the editor (not the picker) so an Undo restores the controls along
 * with the list they produced, and so the same choice can be re-applied by choosing it again.
 */
export interface PickerState {
  outlineLevel: number
  /** 0 = any level. */
  headingLevel: number
  /** In multiples of the body size. */
  threshold: number
  /** Candidates longer than this (characters) are not headings (PRD scope 2). */
  maxLength: number
}

export function initialPicker(analysis: Analysis): PickerState {
  return {
    outlineLevel: initialOutlineLevel(analysis),
    headingLevel: initialHeadingLevel(analysis),
    threshold: MIN_THRESHOLD,
    maxLength: MAX_HEADING_LENGTH,
  }
}

export interface HeadingFilter {
  /** 0 = any level. */
  level: number
  threshold: number
  maxLength?: number
  /** The current bands: a candidate whose top sits inside them is a running head or a folio, not a heading. */
  bands?: Pick<PlanSettings, 'header_band' | 'footer_band'>
}

/**
 * Heading candidates at least `threshold` × body size (and at `level` when `level` > 0), no longer than
 * `maxLength`, outside the header/footer bands. The candidates were detected once at a low threshold with the
 * default bands, so this is instant and needs no request; a band grown past the default drops more here.
 */
export function headingSections(analysis: Analysis, filter: HeadingFilter): Section[] {
  const { level, threshold, maxLength = Infinity, bands } = filter
  const floor = threshold * analysis.headings.body_size
  return analysis.headings.candidates
    .filter((c) => {
      if (c.size < floor || (level !== 0 && c.level !== level) || c.name.length > maxLength) return false
      if (!bands) return true
      const H = analysis.size[c.page - 1]?.H
      return c.y >= bands.header_band && (H === undefined || c.y < H - bands.footer_band)
    })
    .map((c) => section(c.name, c.page, c.heading))
}

/** The slider's top: the biggest heading level as a multiple of the body size, rounded up, at least 2. */
export function thresholdMax(analysis: Analysis): number {
  const { body_size, levels } = analysis.headings
  const top = body_size > 0 ? Math.max(...levels.map((l) => l.size / body_size), 0) : 0
  return Math.max(2, Math.ceil(top * 10) / 10)
}

/** The outline level to start from: the suggested one, else the first level with any item (1 when none). */
export function initialOutlineLevel(analysis: Analysis): number {
  if (analysis.suggested.source === 'outline' && analysis.suggested.level) return analysis.suggested.level
  const i = analysis.outline.levels.findIndex((n) => n > 0)
  return i === -1 ? 1 : i + 1
}

export function initialHeadingLevel(analysis: Analysis): number {
  if (analysis.suggested.source === 'headings' && analysis.suggested.level) return analysis.suggested.level
  return analysis.headings.levels.length ? 1 : 0
}

// ── Paste list ───────────────────────────────────────────────────────────────────────────────────────

export interface LineError {
  /** 1-based line in the pasted text. */
  line: number
  text: string
  message: string
}

export interface ParsedList {
  sections: Section[]
  errors: LineError[]
}

const INTEGER = /^\d+$/

/**
 * `Name, page` per line (the LAST comma splits, so a name may contain commas; a tab works too). Blank lines
 * are skipped. Every bad line gets its own error and the good lines still parse, so the user fixes them in
 * place. `pages` (the book's sheet count) bounds the page when known.
 */
export function parseList(text: string, pages?: number): ParsedList {
  const sections: Section[] = []
  const errors: LineError[] = []
  text.split(/\r?\n/).forEach((raw, i) => {
    const line = raw.trim()
    if (!line) return
    const fail = (message: string) => errors.push({ line: i + 1, text: line, message })
    const at = line.includes('\t') ? line.lastIndexOf('\t') : line.lastIndexOf(',')
    if (at === -1) return fail('Expected "Name, page"')
    const name = line.slice(0, at).trim()
    const pageText = line.slice(at + 1).trim()
    if (!name) return fail('The name is missing')
    if (name.length > MAX_NAME) return fail(`The name is longer than ${MAX_NAME} characters`)
    if (!INTEGER.test(pageText)) return fail(`"${pageText}" is not a page number`)
    const page = Number(pageText)
    if (page < 1) return fail('Pages start at 1')
    if (pages !== undefined && page > pages) return fail(`Page ${page} is past the last page (${pages})`)
    sections.push({ name, page, heading: '' })
  })
  return { sections, errors }
}

/** The inverse of `parseList`, for pre-filling the textarea with the current list. */
export function formatList(sections: Section[]): string {
  return sections.map((s) => `${s.name}, ${s.page}`).join('\n')
}

// ── List operations (each returns a new Plan; overrides re-keyed) ───────────────────────────────────

/** `map(oldIndex)` → the new index, or null when the section is gone. Overrides of gone sections are dropped. */
export function shiftOverrides(overrides: Record<string, Override>, map: (i: number) => number | null): Record<string, Override> {
  const out: Record<string, Override> = {}
  for (const [key, ov] of Object.entries(overrides)) {
    const to = map(Number(key))
    if (to !== null) out[String(to)] = ov
  }
  return out
}

/**
 * A section whose END moved (its neighbour was removed, inserted or merged away) keeps its start override only:
 * an end cut names a y on what used to be its last sheet, and the engine would apply it on the new one.
 */
function dropEnd(overrides: Record<string, Override>, i: number): void {
  const ov = overrides[String(i)]
  if (!ov) return
  const { endCut: _endCut, endCol: _endCol, ...start } = ov
  if (Object.keys(start).length) overrides[String(i)] = start
  else delete overrides[String(i)]
}

export function removeSection(plan: Plan, i: number): Plan {
  const overrides = shiftOverrides(plan.overrides, (k) => (k === i ? null : k > i ? k - 1 : k))
  dropEnd(overrides, i - 1)
  return { ...plan, sections: plan.sections.filter((_, k) => k !== i), overrides }
}

/**
 * Section i absorbs section i+1: it keeps its name and start; the merged section ends where i+1 ended, so
 * i+1's end override (if any) becomes i's end override and i+1's start override is dropped.
 */
export function mergeWithNext(plan: Plan, i: number): Plan {
  if (i < 0 || i + 1 >= plan.sections.length) return plan
  const start = plan.overrides[String(i)] ?? {}
  const next = plan.overrides[String(i + 1)] ?? {}
  const merged: Override = {}
  if ('startCut' in start) merged.startCut = start.startCut
  if ('startCol' in start) merged.startCol = start.startCol
  if ('endCut' in next) merged.endCut = next.endCut
  if ('endCol' in next) merged.endCol = next.endCol
  const overrides = shiftOverrides(plan.overrides, (k) => (k === i || k === i + 1 ? null : k > i + 1 ? k - 1 : k))
  if (Object.keys(merged).length) overrides[String(i)] = merged
  return { ...plan, sections: plan.sections.filter((_, k) => k !== i + 1), overrides }
}

/**
 * Inserts `s` after the last section that starts on or before its page (keeps the list in book order). The new
 * section has no override; the one before it now ends where `s` starts, so its end override goes.
 */
export function insertSection(plan: Plan, s: Section): { plan: Plan; index: number } {
  let index = plan.sections.length
  while (index > 0 && plan.sections[index - 1]!.page > s.page) index--
  const sections = [...plan.sections.slice(0, index), s, ...plan.sections.slice(index)]
  const overrides = shiftOverrides(plan.overrides, (k) => (k >= index ? k + 1 : k))
  dropEnd(overrides, index - 1)
  return { plan: { ...plan, sections, overrides }, index }
}

// ── Flags ────────────────────────────────────────────────────────────────────────────────────────────

/** What a visitor reads for each engine flag; unknown flags show as-is. */
export const FLAG_LABELS: Record<string, string> = {
  'heading-not-found': 'Heading not found on its page',
  'possible-truncation': 'May be cut short',
  'span-clamped': 'Reached the page limit',
  'end-at-known-start': 'Ends where the next section starts',
  override: 'Manual cut',
  leak: 'Text from a neighbour leaked in',
  'long-span': 'Unusually long',
}

export function flagLabel(flag: string): string {
  return FLAG_LABELS[flag] ?? flag
}

/**
 * The flags to badge on a section. The last section always carries `span-clamped` (nothing follows it, so
 * the span hits the engine's cap at the end of the book) — not a warning, so it is dropped there.
 */
export function badgeFlags(row: ManifestRow | undefined, isLast: boolean): string[] {
  if (!row) return []
  return row.flags.filter((f) => !(isLast && f === 'span-clamped'))
}

/**
 * The manifest row for section `i`, only while that section still is the one that was cut: a rename,
 * delete or insert makes the row's index or name disagree and the stale badge disappears.
 */
export function rowFor(rows: ManifestRow[], plan: Plan, i: number): ManifestRow | undefined {
  const s = plan.sections[i]
  return rows.find((r) => r.index === i && s !== undefined && r.name === s.name)
}
