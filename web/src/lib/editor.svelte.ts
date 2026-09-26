/**
 * The plan store: the local Plan, every edit as a method, and the debounced `PUT /plan` behind it (AC-2).
 * One request is in flight at a time; edits made meanwhile are sent after it, and a 200 body only replaces
 * the local plan when nothing changed while the request was out (the server normalizes names, so the body
 * is the truth for what was sent — not for what was typed since). 422 field errors are kept by `loc` so a
 * control can show its own (AC-5); 409 `busy` and network failures are shown, never retried in a loop.
 */
import { ApiError, isGone, type Plan, type PlanSettings, type Section, type Source } from './api'
import { SAVE_DEBOUNCE_MS, UNDO_MS } from './config'
import { insertSection, mergeWithNext, removeSection } from './plan'

export type SavePlan = (plan: Plan) => Promise<Plan>

export interface EditorOptions {
  debounceMs?: number
  undoMs?: number
}

/** `["body", "sections", 0, "page"]` → `"sections.0.page"`: the key a control asks `errorAt` for. */
function locKey(loc: (string | number)[]): string {
  return (loc[0] === 'body' ? loc.slice(1) : loc).join('.')
}

/** Pydantic prefixes a validator's own message with "Value error, "; the sentence after it is the one to show. */
function plainMessage(msg: string): string {
  return msg.replace(/^Value error, /, '')
}

// Never rendered: the constructor replaces it with the loaded plan before anything reads it.
const PLACEHOLDER: Plan = {
  source: 'manual',
  settings: { column_split: 0.487, single_column: false, header_band: 50, footer_band: 32, heading_min_size: 12.5 },
  sections: [],
  overrides: {},
}

export class PlanEditor {
  plan = $state<Plan>(PLACEHOLDER)
  /** Edits not yet acknowledged by the server. */
  dirty = $state(false)
  saving = $state(false)
  /** The last save's failure other than field errors (busy, network, gone…). */
  error = $state<ApiError | null>(null)
  /** 422 `errors[]` from the last save, keyed by `locKey`; cleared by the next 200. */
  fieldErrors = $state<Record<string, string>>({})
  /** The list a source switch replaced, while "Undo" is on offer. */
  undo = $state<{ label: string; plan: Plan } | null>(null)
  /** The section the preview (STORY-010) shows; null until the user picks one. */
  selected = $state<number | null>(null)
  /** 404/410: the job will never answer again. */
  gone = $state(false)

  private readonly save: SavePlan
  private readonly debounceMs: number
  private readonly undoMs: number
  private timer: ReturnType<typeof setTimeout> | undefined
  private undoTimer: ReturnType<typeof setTimeout> | undefined
  private inflight: Promise<void> | null = null
  private queued = false
  /** Bumped by every edit; a response is adopted only when it still matches. */
  private version = 0

  constructor(plan: Plan, save: SavePlan, opts: EditorOptions = {}) {
    this.plan = plan
    this.save = save
    this.debounceMs = opts.debounceMs ?? SAVE_DEBOUNCE_MS
    this.undoMs = opts.undoMs ?? UNDO_MS
  }

  // ── Edits ──

  /** A source switch (AC-2): the whole list and its overrides go, with one Undo back to the list before the run. */
  replaceSections(source: Source, sections: Section[], label: string): void {
    // A run of picker changes (a slider drag) keeps the first snapshot, so Undo returns to where the run began.
    if (!this.undo) this.undo = { label, plan: $state.snapshot(this.plan) }
    else this.undo = { ...this.undo, label }
    clearTimeout(this.undoTimer)
    this.undoTimer = setTimeout(() => (this.undo = null), this.undoMs)
    this.plan = { ...this.plan, source, sections, overrides: {} }
    this.selected = null
    this.touch()
  }

  undoLast(): void {
    if (!this.undo) return
    this.plan = this.undo.plan
    this.undo = null
    clearTimeout(this.undoTimer)
    this.selected = null
    this.touch()
  }

  rename(i: number, name: string): void {
    const s = this.plan.sections[i]
    if (!s) return
    s.name = name
    this.touch()
  }

  setPage(i: number, page: number): void {
    const s = this.plan.sections[i]
    if (!s || !Number.isInteger(page)) return
    s.page = page
    this.touch()
  }

  remove(i: number): void {
    if (!this.plan.sections[i]) return
    this.plan = removeSection($state.snapshot(this.plan), i)
    if (this.selected !== null) this.selected = this.selected === i ? null : this.selected > i ? this.selected - 1 : this.selected
    this.touch()
  }

  merge(i: number): void {
    if (i + 1 >= this.plan.sections.length) return
    this.plan = mergeWithNext($state.snapshot(this.plan), i)
    if (this.selected !== null && this.selected > i) this.selected = this.selected === i + 1 ? i : this.selected - 1
    this.touch()
  }

  /** Inserts in book order and returns the new index. */
  add(name: string, page: number): number {
    const { plan, index } = insertSection($state.snapshot(this.plan), { name, page, heading: '' })
    this.plan = plan
    if (this.selected !== null && this.selected >= index) this.selected++
    this.touch()
    return index
  }

  setSetting<K extends keyof PlanSettings>(key: K, value: PlanSettings[K]): void {
    this.plan.settings[key] = value
    this.touch()
  }

  select(i: number | null): void {
    this.selected = i
  }

  // ── Saving ──

  errorAt(...loc: (string | number)[]): string | null {
    return this.fieldErrors[loc.join('.')] ?? null
  }

  /** Field errors that belong to no single control (`sections`, `overrides`, `source`, or the whole body). */
  get listErrors(): string[] {
    return Object.entries(this.fieldErrors)
      .filter(([key]) => !/^sections\.\d+\./.test(key) && !key.startsWith('settings.'))
      .map(([, msg]) => msg)
  }

  private touch(): void {
    this.dirty = true
    this.version++
    clearTimeout(this.timer)
    this.timer = setTimeout(() => void this.flush(), this.debounceMs)
  }

  /** Sends now (the debounce is skipped); a request already out is followed by one more with the latest plan. */
  flush(): Promise<void> {
    clearTimeout(this.timer)
    if (this.gone || !this.dirty) return this.inflight ?? Promise.resolve()
    if (this.inflight) {
      this.queued = true
      return this.inflight
    }
    this.inflight = this.send().finally(() => {
      this.inflight = null
    })
    return this.inflight.then(() => {
      if (this.queued) {
        this.queued = false
        return this.flush()
      }
    })
  }

  private async send(): Promise<void> {
    const sent = this.version
    this.saving = true
    this.error = null
    try {
      const saved = await this.save($state.snapshot(this.plan))
      this.fieldErrors = {}
      if (this.version === sent) {
        this.plan = saved
        this.dirty = false
      }
    } catch (err) {
      const apiErr = err instanceof ApiError ? err : new ApiError(0, 'network')
      if (apiErr.status === 422 && apiErr.errors) {
        const next: Record<string, string> = {}
        for (const e of apiErr.errors) next[locKey(e.loc)] ??= plainMessage(e.msg)
        this.fieldErrors = next
      } else {
        this.error = apiErr
        if (isGone(apiErr)) this.gone = true
      }
    } finally {
      this.saving = false
    }
  }

  destroy(): void {
    clearTimeout(this.timer)
    clearTimeout(this.undoTimer)
  }
}

