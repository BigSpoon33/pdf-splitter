<script lang="ts">
  import { untrack } from 'svelte'
  import type { Analysis, Section, Source } from '../lib/api'
  import {
    formatList,
    headingSections,
    initialHeadingLevel,
    initialOutlineLevel,
    MIN_THRESHOLD,
    outlineSections,
    parseList,
    thresholdMax,
  } from '../lib/plan'

  interface Props {
    analysis: Analysis
    pages: number
    /** The saved plan's source (checked radio). */
    source: Source
    /** The current list, to pre-fill the paste box when the user switches to it. */
    sections: Section[]
    /** A user choice that replaces the list: the new source, the sections, and a label for the Undo toast. */
    onpick: (source: Source, sections: Section[], label: string) => void
  }

  let { analysis, pages, source, sections, onpick }: Props = $props()

  const outlineLevels = $derived(analysis.outline.levels)
  const hasOutline = $derived(outlineLevels.some((n) => n > 0))
  const hasHeadings = $derived(analysis.headings.candidates.length > 0)

  // The starting levels come from the analysis once; the analysis of a job never changes afterwards.
  let outlineLevel = $state(untrack(() => initialOutlineLevel(analysis)))
  let headingLevel = $state(untrack(() => initialHeadingLevel(analysis)))
  let threshold = $state(MIN_THRESHOLD)
  let pasted = $state('')

  const maxThreshold = $derived(thresholdMax(analysis))
  const headingPick = $derived(headingSections(analysis, headingLevel, threshold))
  const parsed = $derived(parseList(pasted, pages))
  const canUseList = $derived(parsed.errors.length === 0 && parsed.sections.length > 0)

  /** A few titles from a level, so "level 2" reads as "the chapters" rather than a number (Maciocia: L1 = parts). */
  function samples(level: number): string {
    const names = analysis.outline.items.filter((it) => it.level === level).slice(0, 3).map((it) => it.name)
    return names.map((n) => (n.length > 28 ? `${n.slice(0, 28).replace(/\s+\S*$/, '')}…` : n)).join(' · ')
  }

  function pickOutline(level: number) {
    outlineLevel = level
    onpick('outline', outlineSections(analysis, level), `the outline (level ${level})`)
  }

  function pickHeadings() {
    onpick('headings', headingPick, 'the detected headings')
  }

  function choose(next: Source) {
    if (next === 'outline') pickOutline(outlineLevel)
    else if (next === 'headings') pickHeadings()
    else {
      // The current list becomes the pasted one, so switching loses nothing until the user edits the text.
      if (!pasted.trim()) pasted = formatList(sections)
      onpick('manual', parseList(pasted, pages).sections, 'the pasted list')
    }
  }

  function useList() {
    if (canUseList) onpick('manual', parsed.sections, 'the pasted list')
  }

  function number(e: Event): number {
    return Number((e.currentTarget as HTMLInputElement | HTMLSelectElement).value)
  }
</script>

<fieldset class="picker">
  <legend>Find sections from</legend>

  <div class="choice">
    <label>
      <input type="radio" name="source" value="outline" checked={source === 'outline'} disabled={!hasOutline} onchange={() => choose('outline')} />
      Outline
    </label>
    {#if !hasOutline}
      <p class="why">This PDF has no outline (bookmarks), so there are no chapters to read from it.</p>
    {:else if source === 'outline'}
      <label class="field">
        <span>Level</span>
        <select value={outlineLevel} onchange={(e) => pickOutline(number(e))}>
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
      <input type="radio" name="source" value="headings" checked={source === 'headings'} disabled={!hasHeadings} onchange={() => choose('headings')} />
      Headings
    </label>
    {#if !hasHeadings}
      <p class="why">No headings bigger than the body text were found.</p>
    {:else if source === 'headings'}
      <div class="row">
        <label class="field">
          <span>At least {threshold.toFixed(1)}× the body size ({(threshold * analysis.headings.body_size).toFixed(1)} pt)</span>
          <input
            type="range"
            min={MIN_THRESHOLD}
            max={maxThreshold}
            step="0.1"
            value={threshold}
            oninput={(e) => {
              threshold = number(e)
              pickHeadings()
            }}
          />
        </label>
        <label class="field">
          <span>Level</span>
          <select
            value={headingLevel}
            onchange={(e) => {
              headingLevel = number(e)
              pickHeadings()
            }}
          >
            <option value={0}>Any level</option>
            {#each analysis.headings.levels as lvl, i (i)}
              <option value={i + 1}>Level {i + 1} — {lvl.size.toFixed(1)} pt, {lvl.count} found</option>
            {/each}
          </select>
        </label>
        <p class="count" data-testid="heading-count">{headingPick.length} {headingPick.length === 1 ? 'section' : 'sections'}</p>
      </div>
    {/if}
  </div>

  <div class="choice">
    <label>
      <input type="radio" name="source" value="manual" checked={source === 'manual'} onchange={() => choose('manual')} />
      Paste a list
    </label>
    {#if source === 'manual'}
      <label class="field">
        <span>One section per line as <code>Name, page</code> (page = sheet number, counted from 1)</span>
        <textarea rows="8" bind:value={pasted} aria-invalid={parsed.errors.length > 0} aria-describedby="paste-errors"></textarea>
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
