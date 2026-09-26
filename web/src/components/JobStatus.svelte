<script lang="ts">
  import { ApiError, getJob, isGone, TERMINAL_STATES, type JobStatus } from '../lib/api'
  import { POLL_MS } from '../lib/config'
  import { messageFor } from '../lib/errors'

  interface Props {
    id: string
    load?: (id: string, signal: AbortSignal) => Promise<JobStatus>
    pollMs?: number
    /** Every status the poll receives, so the page can mount the review UI once the job is in `review`/`done`. */
    onstatus?: (job: JobStatus) => void
  }

  let { id, load = getJob, pollMs = POLL_MS, onstatus }: Props = $props()

  let job = $state<JobStatus | null>(null)
  /** A final error: the job is gone or the address is wrong. Nothing more to poll. */
  let fatal = $state<string | null>(null)
  /** A transient error: the last poll failed, the next one may not. */
  let hiccup = $state<string | null>(null)

  $effect(() => {
    const ctrl = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined

    async function poll() {
      try {
        const next = await load(id, ctrl.signal)
        if (ctrl.signal.aborted) return
        job = next
        hiccup = null
        onstatus?.(next)
        if (TERMINAL_STATES.has(next.state)) return
      } catch (err) {
        if (ctrl.signal.aborted) return
        if (isGone(err)) {
          fatal = (err as ApiError).userMessage
          return
        }
        hiccup = err instanceof ApiError ? err.userMessage : messageFor(null)
      }
      timer = setTimeout(poll, pollMs)
    }

    void poll()
    return () => {
      ctrl.abort()
      clearTimeout(timer)
    }
  })

  const LABELS: Record<JobStatus['state'], string> = {
    queued: 'Waiting in line',
    running: 'Working',
    review: 'Ready for review',
    done: 'Sections ready',
    failed: 'Failed',
  }

  function label(j: JobStatus): string {
    if (j.state === 'running') return j.kind === 'cut' ? 'Cutting sections' : 'Analyzing'
    if (j.state === 'queued') return j.kind === 'cut' ? 'Waiting to cut' : 'Waiting to analyze'
    return LABELS[j.state]
  }

  const active = $derived(job !== null && !TERMINAL_STATES.has(job.state))
  // Mirrors the loop's stop conditions, so assistive tech is never told to wait for an update that won't come.
  const polling = $derived(!fatal && (job === null || !TERMINAL_STATES.has(job.state)))
  const fraction = $derived(job && job.total > 0 ? Math.min(job.progress / job.total, 1) : null)
</script>

<section class="card" aria-live="polite" aria-busy={polling}>
  {#if fatal}
    <p class="error" role="alert">{fatal}</p>
  {:else if !job}
    {#if hiccup}
      <p class="muted" role="status">{hiccup} Retrying…</p>
    {:else}
      <p class="muted">Loading…</p>
    {/if}
  {:else}
    <p class="file">{job.pages ? `${job.filename} · ${job.pages} pages` : job.filename}</p>
    <h1 class="state state-{job.state}" data-state={job.state}>{label(job)}</h1>

    {#if job.queue_position !== null && job.state === 'queued'}
      <p class="queue">Position in queue: {job.queue_position}</p>
    {/if}

    {#if job.state === 'failed'}
      <p class="error" role="alert">{job.message || messageFor(job.error_code)}</p>
    {:else}
      {#if job.message && active}
        <p class="phase">{job.message}</p>
      {/if}
      {#if active && fraction !== null}
        <div
          class="bar"
          role="progressbar"
          aria-label="Job progress"
          aria-valuemin="0"
          aria-valuemax={job.total}
          aria-valuenow={job.progress}
        >
          <span style:width="{fraction * 100}%"></span>
        </div>
        <p class="count">{job.progress} / {job.total}</p>
      {/if}
      {#if job.state === 'review'}
        <p>The analysis is finished. Keep this page's address: it is the only way back to your job for 24 hours.</p>
      {:else if job.state === 'done'}
        <p>Your sections have been cut. Keep this page's address: files are kept for 24 hours.</p>
      {/if}
    {/if}

    {#if hiccup}
      <p class="muted" role="status">{hiccup} Retrying…</p>
    {/if}
  {/if}
</section>

<style>
  .file {
    margin: 0;
    color: var(--muted);
    overflow-wrap: anywhere;
  }
  .state {
    font-size: 1.35rem;
    margin: 0.25rem 0 0.75rem;
  }
  .state-review,
  .state-done {
    color: var(--ok);
  }
  .state-failed {
    color: var(--danger);
  }
  .phase,
  .queue {
    margin: 0 0 0.5rem;
  }
  .count {
    margin: 0.35rem 0 0;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .muted {
    color: var(--muted);
  }
</style>
