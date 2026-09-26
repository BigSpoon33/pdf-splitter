<script lang="ts">
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
    /** The files follow an older plan than the one on screen. */
    stale: boolean
    cut?: (id: string) => Promise<CreatedJob>
    /** The cut was queued: the page starts polling again to follow it. */
    oncut?: () => void
    ongone?: (err: ApiError) => void
  }

  let { id, editor, job, results, stale, cut = postCut, oncut, ongone }: Props = $props()

  let posting = $state(false)
  let error = $state<string | null>(null)

  const cutting = $derived(job?.kind === 'cut' && (job.state === 'queued' || job.state === 'running'))
  // A failed cut leaves the job outside the states a cut may start from (`EDITABLE`, routes/common.py): no retry here.
  const failed = $derived(job?.kind === 'cut' && job.state === 'failed')
  const count = $derived(editor.plan.sections.length)
  const disabled = $derived(posting || cutting || failed || editor.saving || editor.gone || count === 0)

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
      await cut(id)
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
{#if failed}
  <p class="error" role="alert">The cut failed, so this job can't be split again. Upload the PDF again to retry.</p>
{/if}

{#if cutting && job}
  <div class="progress" role="status" aria-live="polite">
    <p>{job.total > 0 ? `Cutting section ${Math.min(job.progress + 1, job.total)} of ${job.total}…` : 'Waiting to cut…'}</p>
    {#if job.total > 0}
      <div class="bar"><span style:width="{Math.min(job.progress / job.total, 1) * 100}%"></span></div>
    {/if}
  </div>
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
