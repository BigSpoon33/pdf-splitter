/**
 * Pure edits to a Plan: building section lists from the analysis (AC-1), the paste-list parser, and the
 * list operations that shift section indexes (AC-3). Overrides are keyed by section INDEX, so every
 * insert/delete/merge re-keys them here — the API refuses a key past the end
 * (`tests/test_api_e2e.py::test_put_plan_rejects_bad_plans_with_field_errors`).
 * Limits mirror `src/pdf_splitter/models.py` (`Section`, `PlanSettings`).
 */
import type { Analysis, ManifestRow, Override, Plan, Section } from './api'

export const MAX_NAME = 120
export const MAX_HEADING = 500
export const MIN_THRESHOLD = 1

// ── Sources ──────────────────────────────────────────────────────────────────────────────────────────

function section(name: string, page: number, heading: string): Section {
  return { name: name.slice(0, MAX_NAME), page, heading: heading.slice(0, MAX_HEADING) }
}

/** The outline items at exactly `level` (1-based), as the worker's `default_plan` builds them. */
export function outlineSections(analysis: Analysis, level: number): Section[] {
  return analysis.outline.items.filter((it) => it.level === level).map((it) => section(it.name, it.page, it.heading))
}

/**
 * Heading candidates at least `threshold` × body size (and at `level` when `level` > 0). The candidates were
 * detected once at a low threshold, so this is instant and needs no request.
 */
export function headingSections(analysis: Analysis, level: number, threshold: number): Section[] {
  const floor = threshold * analysis.headings.body_size
  return analysis.headings.candidates
    .filter((c) => c.size >= floor && (level === 0 || c.level === level))
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

export function removeSection(plan: Plan, i: number): Plan {
  return {
    ...plan,
    sections: plan.sections.filter((_, k) => k !== i),
    overrides: shiftOverrides(plan.overrides, (k) => (k === i ? null : k > i ? k - 1 : k)),
  }
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

/** Inserts `s` after the last section that starts on or before its page (keeps the list in book order). */
export function insertSection(plan: Plan, s: Section): { plan: Plan; index: number } {
  let index = plan.sections.length
  while (index > 0 && plan.sections[index - 1]!.page > s.page) index--
  const sections = [...plan.sections.slice(0, index), s, ...plan.sections.slice(index)]
  return {
    plan: { ...plan, sections, overrides: shiftOverrides(plan.overrides, (k) => (k >= index ? k + 1 : k)) },
    index,
  }
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
