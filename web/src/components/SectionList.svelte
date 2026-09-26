<script lang="ts">
  import type { Analysis } from '../lib/api'
  import type { PlanEditor } from '../lib/editor.svelte'
  import { badgesFor, flagLabel, MAX_NAME } from '../lib/plan'

  interface Props {
    editor: PlanEditor
    analysis: Analysis
  }

  let { editor, analysis }: Props = $props()

  const sections = $derived(editor.plan.sections)
  const last = $derived(sections.length - 1)

  let newName = $state('')
  let newPage = $state<number | null>(null)
  let addError = $state<string | null>(null)

  /** ADR-003: the sheet number is the page; the printed label is only a hint. */
  function label(page: number): string {
    return analysis.pageLabels[page - 1] ?? ''
  }

  /** What the name input shows: the draft while it is being typed, so a save's normalized name never lands under the cursor. */
  function nameOf(i: number, saved: string): string {
    return editor.draft?.i === i ? editor.draft.name : saved
  }

  function pageOf(e: Event): number {
    return (e.currentTarget as HTMLInputElement).valueAsNumber
  }

  function onNameKey(e: KeyboardEvent) {
    // Enter commits like a form would.
    if (e.key !== 'Enter') return
    editor.commitDraft()
    ;(e.currentTarget as HTMLInputElement).blur()
  }

  function add(e: SubmitEvent) {
    e.preventDefault()
    const name = newName.trim()
    if (!name) return (addError = 'Give the section a name.')
    if (newPage === null || !Number.isInteger(newPage)) return (addError = 'Give the section a start page.')
    if (newPage < 1 || newPage > analysis.pages) return (addError = `The page must be between 1 and ${analysis.pages}.`)
    addError = null
    editor.add(name, newPage)
    newName = ''
    newPage = null
  }
</script>

<div class="head">
  <h2>{sections.length} {sections.length === 1 ? 'section' : 'sections'}</h2>
  {#if sections.length === 0}
    <p class="muted">No sections yet. Choose a source above or add one below.</p>
  {/if}
</div>

{#each editor.listErrors as msg (msg)}
  <p class="field-error" role="alert">{msg}</p>
{/each}

<ol class="list">
  {#each sections as s, i (i)}
    {@const nameError = editor.errorAt('sections', i, 'name')}
    {@const pageError = editor.errorAt('sections', i, 'page')}
    {@const flags = badgesFor(editor.plan, editor.rows, i)}
    <li class="row" class:selected={editor.selected === i}>
      <label class="select">
        <input type="radio" name="selected" value={i} aria-label="Select section {i + 1}" checked={editor.selected === i} onchange={() => editor.select(i)} />
        <span class="num" aria-hidden="true">{i + 1}</span>
      </label>
      <div class="field name">
        <input
          type="text"
          aria-label="Name of section {i + 1}"
          value={nameOf(i, s.name)}
          maxlength={MAX_NAME}
          aria-invalid={nameError !== null}
          aria-describedby={nameError ? `name-error-${i}` : undefined}
          onfocus={(e) => editor.setDraft(i, e.currentTarget.value)}
          oninput={(e) => editor.setDraft(i, e.currentTarget.value)}
          onkeydown={onNameKey}
          onblur={() => editor.commitDraft()}
        />
        {#if nameError}
          <p class="field-error" id="name-error-{i}">{nameError}</p>
        {/if}
      </div>
      <div class="field page">
        <span class="page-line">
          <input
            type="number"
            aria-label="Start page of section {i + 1}"
            value={s.page}
            min="1"
            max={analysis.pages}
            step="1"
            aria-invalid={pageError !== null}
            aria-describedby={pageError ? `page-error-${i}` : undefined}
            onchange={(e) => editor.setPage(i, pageOf(e))}
          />
          {#if label(s.page)}
            <span class="printed" title="The page number printed on the sheet">p. {label(s.page)}</span>
          {/if}
        </span>
        {#if pageError}
          <p class="field-error" id="page-error-{i}">{pageError}</p>
        {/if}
      </div>
      {#if flags.length}
        <ul class="flags" aria-label="Flags of section {i + 1}">
          {#each flags as flag (flag)}
            <li class="badge" class:info={flag === 'override'} title={flag}>{flagLabel(flag)}</li>
          {/each}
        </ul>
      {/if}
      <div class="actions">
        <button type="button" disabled={i === last} onclick={() => editor.merge(i)} title="Absorb the next section into this one">
          Merge ↓
        </button>
        <button type="button" onclick={() => editor.remove(i)} aria-label="Delete section {i + 1}">Delete</button>
      </div>
    </li>
  {/each}
</ol>

<!-- novalidate: the range error is shown inline in the same style as the API's, not as the browser's tooltip -->
<form class="add" onsubmit={add} novalidate>
  <label class="field name">
    <span>New section</span>
    <input type="text" bind:value={newName} maxlength={MAX_NAME} placeholder="Name" />
  </label>
  <label class="field page">
    <span>Starts on sheet</span>
    <input type="number" bind:value={newPage} min="1" max={analysis.pages} step="1" placeholder="1–{analysis.pages}" />
  </label>
  <button type="submit">Add section</button>
  {#if addError}
    <p class="field-error" role="alert">{addError}</p>
  {/if}
</form>

<style>
  .head h2 {
    font-size: 1.1rem;
    margin: 0 0 0.5rem;
  }
  .muted {
    color: var(--muted);
    margin: 0 0 0.5rem;
  }
  .list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .row {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    grid-template-areas:
      'sel name page'
      'sel flags flags'
      'sel actions actions';
    gap: 0.35rem 0.6rem;
    align-items: start;
    padding: 0.5rem;
    border: 1px solid var(--border);
    border-radius: 8px;
  }
  .row.selected {
    border-color: var(--accent);
    background: var(--accent-soft);
  }
  .select {
    grid-area: sel;
    display: flex;
    align-items: center;
    gap: 0.35rem;
    padding-top: 0.45rem;
  }
  .num {
    color: var(--muted);
    font-variant-numeric: tabular-nums;
    min-width: 1.5rem;
  }
  .name {
    grid-area: name;
  }
  .name input {
    width: 100%;
  }
  .page {
    grid-area: page;
  }
  .page-line {
    display: flex;
    align-items: center;
    gap: 0.4rem;
  }
  .page input {
    width: 5.5rem;
  }
  .printed {
    color: var(--muted);
    font-size: 0.85rem;
    white-space: nowrap;
  }
  .flags {
    grid-area: flags;
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }
  .actions {
    grid-area: actions;
    display: flex;
    gap: 0.4rem;
  }
  .add {
    display: flex;
    flex-wrap: wrap;
    align-items: end;
    gap: 0.6rem;
    margin-top: 1rem;
    padding-top: 0.75rem;
    border-top: 1px solid var(--border);
  }
  .add .name {
    flex: 1 1 12rem;
  }
  .add .page input {
    width: 6rem;
  }
  .add .field-error {
    flex-basis: 100%;
  }
</style>
