<script lang="ts">
  import { untrack } from 'svelte'
  import type { PlanEditor } from '../lib/editor.svelte'
  import { everyN, formatRanges, parseRanges, rangeSections, spansOf } from '../lib/ranges'

  interface Props {
    editor: PlanEditor
    /** The book's sheet count: the upper bound of every range. */
    pages: number
  }

  let { editor, pages }: Props = $props()

  const sections = $derived(editor.plan.sections)
  /** The field shows the text as typed; until the user types (null) it shows the saved spans, so a reload reads back. */
  let typed = $state<string | null>(null)
  const text = $derived(typed ?? formatRanges(sections.map((s) => ({ page: s.page, endPage: s.endPage ?? s.page }))))
  const parsed = $derived(parseRanges(text, pages))
  let every = $state<number | null>(null)
  const canFill = $derived(every !== null && Number.isInteger(every) && every >= 1)
  /** A 422 on a span's `endPage` (`sections.N.endPage`): the list has no control for it, the field here owns the spans. */
  const saveErrors = $derived(
    Object.entries(editor.fieldErrors)
      .filter(([key]) => /^sections\.\d+\.endPage$/.test(key))
      .map(([key, message]) => ({ i: Number(key.split('.')[1]), message })),
  )

  // The list can change without the field (a row deleted below): then the field follows the list. The field's own
  // applies leave the text as typed — its spans already match — so spacing and separators never jump under the cursor.
  let lastSpans = spansOf(untrack(() => sections))
  $effect(() => {
    const spans = spansOf(sections)
    untrack(() => {
      if (spans === lastSpans) return
      lastSpans = spans
      if (spans !== spansOf(parsed.ranges)) typed = null
    })
  })

  /**
   * Every keystroke re-parses; the plan follows only a text with no bad token, so a half-typed `15-2` on the way to
   * `15-20` never becomes a save (or a cut of a list the visitor is still typing). The errors show meanwhile.
   */
  function apply(next: string) {
    typed = next
    const { ranges, errors } = parseRanges(next, pages)
    if (errors.length) return
    const list = rangeSections(ranges, editor.plan.sections)
    const spans = spansOf(list)
    if (spans === spansOf(editor.plan.sections)) return
    lastSpans = spans
    editor.setRanges(list)
  }

  function fill(e: SubmitEvent) {
    e.preventDefault()
    if (!canFill || every === null) return
    apply(formatRanges(everyN(every, pages)))
  }
</script>

<h2>Page ranges</h2>
<div class="field">
  <label for="ranges-text">Pages to keep, one range or page per entry</label>
  <input
    id="ranges-text"
    type="text"
    value={text}
    placeholder="1-10, 11-25, 40"
    spellcheck="false"
    autocomplete="off"
    aria-invalid={parsed.errors.length > 0 || saveErrors.length > 0}
    aria-describedby="ranges-help ranges-errors"
    oninput={(e) => apply(e.currentTarget.value)}
  />
  <p id="ranges-help" class="hint">
    Separate entries with commas. Ranges may overlap or leave pages out; each one becomes its own PDF. The document has
    {pages} {pages === 1 ? 'page' : 'pages'}.
  </p>
  <ul id="ranges-errors" class="token-errors" aria-live="polite">
    {#each parsed.errors as err, i (i)}
      <li class="field-error"><code>{err.token}</code> — {err.message}</li>
    {/each}
    {#each saveErrors as err (err.i)}
      <li class="field-error">Range {err.i + 1} — {err.message}</li>
    {/each}
  </ul>
</div>

<!-- novalidate: a bad N just leaves Fill disabled; nothing to shout about -->
<form class="every" onsubmit={fill} novalidate>
  <label class="field">
    <span>Or split every</span>
    <input type="number" bind:value={every} min="1" max={pages} step="1" placeholder="10" aria-label="Pages per file" />
  </label>
  <span class="unit">pages</span>
  <button type="submit" disabled={!canFill}>Fill the ranges</button>
</form>

<style>
  h2 {
    font-size: 1.1rem;
    margin: 0 0 0.75rem;
  }
  .field label {
    font-size: 0.9rem;
    color: var(--muted);
  }
  .field input[type='text'] {
    width: 100%;
    font-variant-numeric: tabular-nums;
  }
  .hint {
    margin: 0;
    color: var(--muted);
    font-size: 0.85rem;
  }
  .token-errors {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }
  .token-errors:empty {
    display: none;
  }
  code {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .every {
    display: flex;
    flex-wrap: wrap;
    align-items: end;
    gap: 0.6rem;
    margin-top: 1rem;
    padding-top: 0.75rem;
    border-top: 1px solid var(--border);
  }
  .every input {
    width: 5.5rem;
  }
  .unit {
    padding-bottom: 0.45rem;
    color: var(--muted);
  }
</style>
