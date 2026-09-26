<script lang="ts">
  import { untrack } from 'svelte'
  import { ApiError, isGone, postCut, resultUrl, sectionUrl, type CreatedJob, type JobStatus, type ManifestRow } from '../lib/api'
  import type { PlanEditor } from '../lib/editor.svelte'
  import { messageFor } from '../lib/errors'
  import { formatBytes } from '../lib/expiry'
  import { badgeFlags, flagLabel } from '../lib/plan'

  interface Props {
    id: string
    editor: PlanEditor
    /** The latest status: the cut's progress comes from the same poll the status card renders. */
    job: JobStatus | null
    /** The last cut's rows; null before any cut. They stay listed after an edit — the files are valid until the next cut. */
    results: ManifestRow[] | null
    /** The last cut's rows could not be read; `onretry` asks again. */
    resultsError?: string | null
    /** A refresh is in flight (the automatic retry, or the button's). */
    retrying?: boolean
    /** The files follow an older plan than the one on screen. */
    stale: boolean
    cut?: (id: string) => Promise<CreatedJob>
    /** The cut was queued: the page starts polling again to follow it. */
    oncut?: () => void
    onretry?: () => void
    ongone?: (err: ApiError) => void
  }

  let { id, editor, job, results, resultsError = null, retrying = false, stale, cut = postCut, oncut, onretry, ongone }: Props = $props()

  let posting = $state(false)
  let error = $state<string | null>(null)
  /**
   * The status on screen when the API accepted this click's cut: Split stays disabled until a later one arrives, so
   * the gap between the 202 and the first poll cannot take a second click (gate r1). `posting` covers the POST itself.
   */
  let requestedOn = $state<JobStatus | null | undefined>(undefined)

  const cutting = $derived(job?.kind === 'cut' && (job.state === 'queued' || job.state === 'running'))
  // A failed cut is recoverable (Architecture § Job states): the API takes `PUT /plan` and `POST /cut` from it like
  // from `review`, so the reason is shown here, above the button, and Split stays live for another try.
  const failed = $derived(job?.kind === 'cut' && job.state === 'failed')
  const count = $derived(editor.plan.sections.length)
  const requested = $derived(requestedOn !== undefined)
  const disabled = $derived(posting || requested || cutting || editor.saving || editor.gone || count === 0)

  // Each status is a new object, and the page restarts its poll on the 202, so every one after `requestedOn` is the
  // server's word from after the cut was queued: a `cut` of any state — `review` included, when an edit landed
  // between the cut's last section and the poll's first answer (gate r2) — or a failure frees the button. Any split
  // error that came before it is history.
  $effect(() => {
    const now = job
    if (now === untrack(() => requestedOn)) return
    error = null
    if (now?.kind === 'cut' || now?.state === 'failed') requestedOn = undefined
  })

  async function split() {
    if (disabled) return
    posting = true
    error = null
    try {
      // The API cuts the SAVED plan, so what is on screen goes first.
      editor.commitDraft()
      await editor.flush()
      if (editor.gone) return
      if (editor.error || editor.dirty || Object.keys(editor.fieldErrors).length) {
        error = 'Fix the section list first: the plan on screen has not been saved.'
        return
      }
      try {
        await cut(id)
      } catch (err) {
        // `busy` means a cut is already running — ours, from a click the answer to which never arrived — so it is
        // followed like any other; every other refusal leaves the button free for another try.
        if (!(err instanceof ApiError && err.code === 'busy')) throw err
      }
      // Anchored now, not at the click: a status that answered during the POST still describes the job before it.
      requestedOn = job
      // The cut follows the plan as saved now; only edits from here on make its files stale.
      editor.edited = false
      oncut?.()
    } catch (err) {
      if (isGone(err)) ongone?.(err as ApiError)
      else error = err instanceof ApiError ? err.userMessage : messageFor(null)
    } finally {
      posting = false
    }
  }
</script>

<h2>Split</h2>
<div class="actions">
  <button type="button" class="primary" onclick={() => void split()} {disabled}>
    {count === 1 ? 'Split into 1 PDF' : `Split into ${count} PDFs`}
  </button>
  {#if posting}
    <span class="muted" role="status">Starting the cut…</span>
  {/if}
</div>
{#if error}
  <p class="error" role="alert">{error}</p>
{/if}
{#if failed && job}
  <p class="error" role="alert">
    The last cut failed: {job.message || messageFor(job.error_code)} Change the sections if you need to, then split again.
  </p>
{/if}

{#if cutting && job}
  <div class="progress" role="status" aria-live="polite">
    <p>{job.total > 0 ? `Cutting section ${Math.min(job.progress + 1, job.total)} of ${job.total}…` : 'Waiting to cut…'}</p>
    {#if job.total > 0}
      <div class="bar"><span style:width="{Math.min(job.progress / job.total, 1) * 100}%"></span></div>
    {/if}
  </div>
{:else if resultsError}
  <p class="error" role="alert">
    <span>The list of your files could not be loaded. {resultsError}</span>
    <button type="button" onclick={() => onretry?.()} disabled={retrying}>{retrying ? 'Retrying…' : 'Retry'}</button>
  </p>
{:else if results && results.length}
  <div class="results">
    <h3>{stale ? 'Files from the last cut' : 'Your files'}</h3>
    <a class="zip" href={resultUrl(id)} download>Download all (ZIP)</a>
    <ul>
      {#each results as row, k (row.index)}
        {@const flags = badgeFlags(row, k === results.length - 1)}
        <li>
          <a href={sectionUrl(id, row.index)} download>{row.file}</a>
          <span class="size">{formatBytes(row.bytes)}</span>
          {#each flags as flag (flag)}
            <span class="badge" class:info={flag === 'override'}>{flagLabel(flag)}</span>
          {/each}
        </li>
      {/each}
    </ul>
  </div>
{/if}

<style>
  h2 {
    font-size: 1.1rem;
    margin: 0 0 0.75rem;
  }
  h3 {
    font-size: 1rem;
    margin: 0;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.75rem;
  }
  .muted {
    color: var(--muted);
  }
  p.error {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.75rem;
  }
  .progress {
    margin-top: 1rem;
  }
  .progress p {
    margin: 0 0 0.4rem;
  }
  .results {
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
    gap: 0.75rem;
    margin-top: 1.25rem;
  }
  .zip {
    justify-self: end;
    padding: 0.35rem 0.8rem;
    border-radius: 8px;
    background: var(--accent);
    color: #fff;
    text-decoration: none;
    font-weight: 600;
  }
  .zip:focus-visible {
    outline: none;
    box-shadow: var(--focus);
  }
  ul {
    grid-column: 1 / -1;
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }
  li {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.25rem 0.6rem;
  }
  li a {
    overflow-wrap: anywhere;
  }
  .size {
    color: var(--muted);
    font-size: 0.85rem;
    font-variant-numeric: tabular-nums;
  }
</style>
