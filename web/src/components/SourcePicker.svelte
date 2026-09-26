<script lang="ts">
  import type { Analysis, PlanSettings, Section, Source } from '../lib/api'
  import {
    formatList,
    headingSections,
    MAX_HEADING_LENGTH,
    MIN_THRESHOLD,
    outlineSections,
    parseList,
    thresholdMax,
    type PickerState,
  } from '../lib/plan'

  interface Props {
    analysis: Analysis
    pages: number
    /** The saved plan's source. */
    source: Source
    /** The current list: the paste box is seeded from it, and the headings count is compared with it. */
    sections: Section[]
    /** The controls' state, owned by the editor so an Undo restores it with the list (see `PlanEditor.picker`). */
    picker: PickerState
    /** The current bands: candidates inside them are not headings. */
    settings: Pick<PlanSettings, 'header_band' | 'footer_band'>
    /** A user choice that replaces the list: the new source, the sections, a label for the Undo toast, the controls. */
    onpick: (source: Source, sections: Section[], label: string, picker: PickerState) => void
  }

  let { analysis, pages, source, sections, picker, settings, onpick }: Props = $props()

  const outlineLevels = $derived(analysis.outline.levels)
  const hasOutline = $derived(outlineLevels.some((n) => n > 0))
  const hasHeadings = $derived(analysis.headings.candidates.length > 0)

  /**
   * "Paste a list" is a view, not a pick: choosing it changes no section (only "Use this list" does), so the
   * expanded radio is local until the saved source moves on (a pick, an Undo) — then the saved source wins.
   */
  let local = $state<{ mode: Source; forSource: Source } | null>(null)
  const mode = $derived(local?.forSource === source ? local.mode : source)

  /** The box shows the current list until the user types; null = untouched, so a fresh list re-seeds it. */
  let typed = $state<string | null>(null)
  const pasted = $derived(typed ?? formatList(sections))

  const maxThreshold = $derived(thresholdMax(analysis))
  const headingFilter = $derived({
    level: picker.headingLevel,
    threshold: picker.threshold,
    maxLength: picker.maxLength,
    bands: settings,
  })
  const headingPick = $derived(headingSections(analysis, headingFilter))
  /** The bands changed under a headings list: the count no longer describes the list until it is re-applied. */
  const headingsDiffer = $derived(
    source === 'headings' && (headingPick.length !== sections.length || headingPick.some((s, i) => s.page !== sections[i]?.page)),
  )
  const parsed = $derived(parseList(pasted, pages))
  const canUseList = $derived(parsed.errors.length === 0 && parsed.sections.length > 0)

  /** A few titles from a level, so "level 2" reads as "the chapters" rather than a number (Maciocia: L1 = parts). */
  function samples(level: number): string {
    const names = analysis.outline.items.filter((it) => it.level === level).slice(0, 3).map((it) => it.name)
    return names.map((n) => (n.length > 28 ? `${n.slice(0, 28).replace(/\s+\S*$/, '')}…` : n)).join(' · ')
  }

  function pickOutline(level: number) {
    local = null
    onpick('outline', outlineSections(analysis, level), `the outline (level ${level})`, { ...picker, outlineLevel: level })
  }

  function pickHeadings(next: Partial<PickerState> = {}) {
    local = null
    const state = { ...picker, ...next }
    onpick('headings', headingSections(analysis, { ...headingFilter, level: state.headingLevel, threshold: state.threshold, maxLength: state.maxLength }), 'the detected headings', state)
  }

  function choose(next: Source) {
    if (next === 'outline') pickOutline(picker.outlineLevel)
    else if (next === 'headings') pickHeadings()
    else {
      local = { mode: 'manual', forSource: source }
      typed = null
    }
  }

  function useList() {
    if (!canUseList) return
    const sections = parsed.sections
    // The box follows the list again, so it shows the names as the server normalized them.
    typed = null
    onpick('manual', sections, 'the pasted list', picker)
  }

  function number(e: Event): number {
    return Number((e.currentTarget as HTMLInputElement | HTMLSelectElement).value)
  }
</script>

<fieldset class="picker">
  <legend>Find sections from</legend>

  <div class="choice">
    <label>
      <input type="radio" name="source" value="outline" checked={mode === 'outline'} disabled={!hasOutline} onchange={() => choose('outline')} />
      Outline
    </label>
    {#if !hasOutline}
      <p class="why">This PDF has no outline (bookmarks), so there are no chapters to read from it.</p>
    {:else if mode === 'outline'}
      <label class="field">
        <span>Level</span>
        <select value={picker.outlineLevel} onchange={(e) => pickOutline(number(e))}>
          {#each outlineLevels as count, i (i)}
            <option value={i + 1} disabled={count === 0}>
              Level {i + 1} — {count} {count === 1 ? 'item' : 'items'}{count ? `: ${samples(i + 1)}` : ''}
            </option>
          {/each}
        </select>
      </label>
    {/if}
  </div>

  <div class="choice">
    <label>
      <input type="radio" name="source" value="headings" checked={mode === 'headings'} disabled={!hasHeadings} onchange={() => choose('headings')} />
      Headings
    </label>
    {#if !hasHeadings}
      <p class="why">No headings bigger than the body text were found.</p>
    {:else if mode === 'headings'}
      <div class="row">
        <label class="field">
          <span>At least {picker.threshold.toFixed(1)}× the body size ({(picker.threshold * analysis.headings.body_size).toFixed(1)} pt)</span>
          <input
            type="range"
            min={MIN_THRESHOLD}
            max={maxThreshold}
            step="0.1"
            value={picker.threshold}
            oninput={(e) => pickHeadings({ threshold: number(e) })}
          />
        </label>
        <label class="field">
          <span>Level</span>
          <select value={picker.headingLevel} onchange={(e) => pickHeadings({ headingLevel: number(e) })}>
            <option value={0}>Any level</option>
            {#each analysis.headings.levels as lvl, i (i)}
              <option value={i + 1}>Level {i + 1} — {lvl.size.toFixed(1)} pt, {lvl.count} found</option>
            {/each}
          </select>
        </label>
        <label class="field">
          <span>Max heading length (characters)</span>
          <input
            type="number"
            min="1"
            max={MAX_HEADING_LENGTH}
            step="1"
            value={picker.maxLength}
            onchange={(e) => {
              const v = number(e)
              if (Number.isInteger(v) && v >= 1) pickHeadings({ maxLength: v })
            }}
          />
        </label>
        <p class="count">
          <span data-testid="heading-count">{headingPick.length} {headingPick.length === 1 ? 'section' : 'sections'}</span>
          <span>outside the header and footer bands</span>
          {#if headingsDiffer}
            <button type="button" onclick={() => pickHeadings()}>Use these headings</button>
          {/if}
        </p>
      </div>
    {/if}
  </div>

  <div class="choice">
    <label>
      <input type="radio" name="source" value="manual" checked={mode === 'manual'} onchange={() => choose('manual')} />
      Paste a list
    </label>
    {#if mode === 'manual'}
      <label class="field">
        <span>One section per line as <code>Name, page</code> (page = sheet number, counted from 1)</span>
        <textarea
          rows="8"
          value={pasted}
          oninput={(e) => (typed = e.currentTarget.value)}
          aria-invalid={parsed.errors.length > 0}
          aria-describedby="paste-errors"
        ></textarea>
      </label>
      <ul id="paste-errors" class="line-errors" aria-live="polite">
        {#each parsed.errors as err (err.line)}
          <li class="field-error">Line {err.line}: {err.message} — <code>{err.text}</code></li>
        {/each}
      </ul>
      <button type="button" class="primary" disabled={!canUseList} onclick={useList}>
        Use this list ({parsed.sections.length})
      </button>
    {/if}
  </div>
</fieldset>

<style>
  .picker {
    border: 0;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  legend {
    font-weight: 600;
    margin-bottom: 0.5rem;
  }
  .choice {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .choice > label:first-child {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-weight: 500;
  }
  .why,
  .count {
    margin: 0;
    color: var(--muted);
    font-size: 0.9rem;
  }
  .count {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    flex-basis: 100%;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 1rem;
    align-items: end;
  }
  .row .field {
    flex: 1 1 14rem;
  }
  .line-errors {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  code {
    font-size: 0.85em;
  }
  select {
    max-width: 100%;
  }
</style>
