/**
 * The plan store: the local Plan, every edit as a method, and the debounced `PUT /plan` behind it (AC-2).
 * One request is in flight at a time; edits made meanwhile are sent after it, and a 200 body only replaces
 * the local plan when nothing changed while the request was out (the server normalizes names, so the body
 * is the truth for what was sent — not for what was typed since). 422 field errors are kept by `loc` so a
 * control can show its own (AC-5); 409 `busy` and network failures are shown, never retried in a loop.
 * Leaving the page never loses an edit silently: `destroy()` and a hidden tab send what is pending as ordinary
 * requests (the page lives on); `pagehide` sends it with `keepalive` when the body is small enough for the browser
 * to carry after unload, otherwise best-effort — and `beforeunload` asks the browser to warn while an edit is
 * unsaved, so a large plan is never lost without the user being told.
 */
import { ApiError, fitsKeepalive, isGone, type ManifestRow, type Override, type Plan, type PlanSettings, type Section, type Source } from './api'
import { SAVE_DEBOUNCE_MS, UNDO_MS } from './config'
import { insertSection, mergeWithNext, removeSection, type PickerState } from './plan'

export interface SendOptions {
  keepalive?: boolean
}

export type SavePlan = (plan: Plan, opts?: SendOptions) => Promise<Plan>

export type UnloadEventType = 'pagehide' | 'visibilitychange' | 'beforeunload'

/** The page's unload events: the real window in the app, a stand-in in tests. */
export interface UnloadSource {
  addEventListener(type: UnloadEventType, listener: (event: { preventDefault(): void }) => void): void
  removeEventListener(type: UnloadEventType, listener: (event: { preventDefault(): void }) => void): void
  document?: { visibilityState: DocumentVisibilityState }
}

export interface EditorOptions {
  debounceMs?: number
  undoMs?: number
  picker?: PickerState
  page?: UnloadSource
}

/** What an Undo puts back: the list and where it came from — never the settings edited since (they are the user's). */
interface UndoSnapshot {
  label: string
  source: Source
  sections: Section[]
  overrides: Plan['overrides']
  picker: PickerState
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

const DEFAULT_PICKER: PickerState = { outlineLevel: 1, headingLevel: 0, threshold: 1, maxLength: 90 }

export class PlanEditor {
  plan = $state<Plan>(PLACEHOLDER)
  /** The picker's controls, snapshotted with the list they produced (an Undo restores both). */
  picker = $state<PickerState>(DEFAULT_PICKER)
  /** The last cut's manifest rows (badges), `[]` before any cut; a merge drops the row it no longer describes. */
  rows = $state<ManifestRow[]>([])
  /** Edits not yet acknowledged by the server. */
  dirty = $state(false)
  /** Any edit since the plan was loaded — the badges from the last cut describe an older plan from then on. */
  edited = $state(false)
  saving = $state(false)
  /** The last save's failure other than field errors (busy, network, gone…). */
  error = $state<ApiError | null>(null)
  /** 422 `errors[]` from the last save, keyed by `locKey`; cleared by the next 200. */
  fieldErrors = $state<Record<string, string>>({})
  /** The list a source switch replaced, while "Undo" is on offer. */
  undo = $state<UndoSnapshot | null>(null)
  /** The section the preview (STORY-010) shows; null until the user picks one. */
  selected = $state<number | null>(null)
  /** Plans the server accepted so far: the preview asks the engine again once an edit has landed, not before. */
  saves = $state(0)
  /** A name being typed: it reaches the plan on commit (blur/Enter/leaving), never rewritten under the cursor. */
  draft = $state<{ i: number; name: string } | null>(null)
  /** 404/410: the job will never answer again. */
  gone = $state(false)

  private readonly save: SavePlan
  private readonly debounceMs: number
  private readonly undoMs: number
  private readonly page: UnloadSource | undefined
  private timer: ReturnType<typeof setTimeout> | undefined
  private undoTimer: ReturnType<typeof setTimeout> | undefined
  private inflight: Promise<void> | null = null
  private queued = false
  /** An edit no request has carried yet (distinct from `dirty`, which also covers a request still out). */
  private unsent = false
  /** Bumped by every edit; a response is adopted only when it still matches. */
  private version = 0
  private readonly onHide = () => this.sendBeforeUnload()
  /**
   * A hidden tab is not an unload: the page keeps running, so an ordinary flush completes whatever its size. A tab
   * coming back retries an edit a failed send left behind (a refused keepalive, a dropped connection).
   */
  private readonly onVisibility = () => {
    if (!this.page?.document) return
    if (this.page.document.visibilityState === 'hidden') {
      this.commitDraft()
      void this.flush()
    } else if (this.unsent && this.error) void this.flush()
  }
  /** The browser's "leave site?" prompt while an edit is unsaved or still out: the only warning a lost save gets. */
  private readonly onBeforeUnload = (event: { preventDefault(): void }) => {
    this.commitDraft()
    if (this.dirty && !this.gone) event.preventDefault()
  }

  constructor(plan: Plan, save: SavePlan, opts: EditorOptions = {}) {
    this.plan = plan
    this.save = save
    this.debounceMs = opts.debounceMs ?? SAVE_DEBOUNCE_MS
    this.undoMs = opts.undoMs ?? UNDO_MS
    this.picker = opts.picker ?? DEFAULT_PICKER
    this.page = opts.page ?? (typeof window === 'undefined' ? undefined : window)
    this.page?.addEventListener('pagehide', this.onHide)
    this.page?.addEventListener('visibilitychange', this.onVisibility)
    this.page?.addEventListener('beforeunload', this.onBeforeUnload)
  }

  // ── Edits ──

  /**
   * A source switch (AC-2): the whole list and its overrides go, with one Undo back to the list before the run.
   * `picker` is the control state that produced `sections`, so Undo can put the controls back too.
   */
  replaceSections(source: Source, sections: Section[], label: string, picker: PickerState = this.picker): void {
    // A run of picker changes (a slider drag) keeps the first snapshot, so Undo returns to where the run began.
    if (!this.undo) {
      const { source: from, sections: list, overrides } = $state.snapshot(this.plan)
      this.undo = { label, source: from, sections: list, overrides, picker: $state.snapshot(this.picker) }
    } else this.undo = { ...this.undo, label }
    clearTimeout(this.undoTimer)
    this.undoTimer = setTimeout(() => (this.undo = null), this.undoMs)
    this.picker = picker
    this.plan = { ...this.plan, source, sections, overrides: {} }
    this.selected = null
    this.touch()
  }

  /**
   * Page-range mode (ADR-009): the list IS the text field's spans, so each parse replaces it whole — no Undo (the
   * text is still there to edit), no overrides (a span has no cuts). Only the range field calls this: a plan is a
   * `ranges` one because the API wrote it so at upload, never because a load switched it.
   */
  setRanges(sections: Section[]): void {
    this.plan = { ...this.plan, source: 'ranges', sections, overrides: {} }
    this.selected = null
    this.touch()
  }

  /** Only the picker's controls changed (a filter that narrows the count): nothing to save. */
  setPicker(picker: PickerState): void {
    this.picker = picker
  }

  undoLast(): void {
    if (!this.undo) return
    const { source, sections, overrides, picker } = this.undo
    this.plan = { ...this.plan, source, sections, overrides }
    this.picker = picker
    this.undo = null
    clearTimeout(this.undoTimer)
    this.selected = null
    this.touch()
  }

  rename(i: number, name: string): void {
    const s = this.plan.sections[i]
    if (!s || s.name === name) return
    s.name = name
    this.touch()
  }

  /** The name input for section `i` took focus or a keystroke: the text is held here until it commits. */
  setDraft(i: number, name: string): void {
    if (this.draft && this.draft.i !== i) this.commitDraft()
    this.draft = { i, name }
  }

  commitDraft(): void {
    if (!this.draft) return
    const { i, name } = this.draft
    this.draft = null
    this.rename(i, name)
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
    // The row for i described the span before the merge; the later rows no longer line up by index anyway.
    this.rows = this.rows.filter((r) => r.index !== i)
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

  /**
   * The manual cut of section `i` (AC-2/AC-4): the whole override is replaced, so a caller keeps the keys it does
   * not change. `null` or an empty override deletes the key — an absent key is "the engine's own plan" for the
   * API, while `{startCut: null}` would remove a cut the engine found.
   */
  setOverride(i: number, override: Override | null): void {
    if (!this.plan.sections[i]) return
    const key = String(i)
    if (override && Object.keys(override).length) this.plan.overrides[key] = override
    else if (key in this.plan.overrides) delete this.plan.overrides[key]
    else return
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
    this.edited = true
    this.unsent = true
    this.version++
    clearTimeout(this.timer)
    this.timer = setTimeout(() => void this.flush(), this.debounceMs)
  }

  /** Sends now (the debounce is skipped); a request already out is followed by one more with the latest plan. */
  flush(): Promise<void> {
    clearTimeout(this.timer)
    if (this.gone || !this.dirty) return this.inflight ?? Promise.resolve()
    if (this.inflight) {
      if (this.unsent) this.queued = true
      return this.inflight
    }
    return this.track(this.send())
  }

  /** Makes `send` the request in flight; when it settles, a plan queued behind it goes next. */
  private track(send: Promise<void>): Promise<void> {
    const done: Promise<void> = send
      .finally(() => {
        // An unload send may have replaced this one meanwhile; only the newest clears the slot.
        if (this.inflight === done) this.inflight = null
      })
      .then(() => {
        if (this.queued && !this.inflight) {
          this.queued = false
          return this.flush()
        }
      })
    this.inflight = done
    return done
  }

  /**
   * The page is unloading: a request still out may be cut short and nothing will run after it, so an unsent edit
   * goes now, ahead of the one-in-flight rule (the older request left first). `keepalive` lets the browser finish
   * it after unload, but only for a body under the keepalive budget — a bigger one is refused outright, so it goes
   * as an ordinary PUT that may or may not complete (the `beforeunload` prompt is what covers that case). It takes
   * the in-flight slot so the `visibilitychange → hidden` that follows `pagehide` on unload does not send it again.
   */
  private sendBeforeUnload(): void {
    this.commitDraft()
    if (this.gone || !this.unsent) return
    clearTimeout(this.timer)
    this.queued = false
    void this.track(this.send({ keepalive: fitsKeepalive($state.snapshot(this.plan)) }))
  }

  private async send(opts: SendOptions = {}): Promise<void> {
    const sent = this.version
    this.unsent = false
    this.saving = true
    this.error = null
    try {
      const saved = await this.save($state.snapshot(this.plan), opts)
      this.fieldErrors = {}
      if (this.version === sent) {
        this.plan = saved
        this.dirty = false
      }
      this.saves++
    } catch (err) {
      // Not accepted: the edit is pending again, so the next flush (a Retry, unload, the tab returning) carries it.
      this.unsent = true
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

  /** The component is going away: a pending save is sent (not awaited), never dropped. */
  destroy(): void {
    this.page?.removeEventListener('pagehide', this.onHide)
    this.page?.removeEventListener('visibilitychange', this.onVisibility)
    this.page?.removeEventListener('beforeunload', this.onBeforeUnload)
    clearTimeout(this.undoTimer)
    this.commitDraft()
    void this.flush()
  }
}
