<script lang="ts">
  import type { ComponentProps } from 'svelte'
  import { getJob, TERMINAL_STATES, type ApiError, type JobStatus as Status } from '../lib/api'
  import { EXPIRY_TICK_MS, WAKE_RECHECK_MS } from '../lib/config'
  import { linkClick, type JobMode } from '../lib/route'
  import Expired, { type GoneReason } from './Expired.svelte'
  import Expiry from './Expiry.svelte'
  import JobStatus from './JobStatus.svelte'
  import Review from './Review.svelte'

  interface Props {
    id: string
    /** `ranges` when the URL says so (ADR-009); Review makes it the plan's `source` on the first load. */
    mode?: JobMode
    /** Everything Review, Expiry and JobStatus load, injectable so the page's state transitions are testable. */
    load?: (id: string, signal: AbortSignal) => Promise<Status>
    review?: Partial<ComponentProps<typeof Review>>
    expiry?: Partial<ComponentProps<typeof Expiry>>
    pollMs?: number
    /** How long a settled page goes without asking the API for its status again. */
    recheckMs?: number
    /** How often the settled page compares its two clocks for a suspend it slept through. */
    tickMs?: number
  }

  let { id, mode, load = getJob, review = {}, expiry = {}, pollMs, recheckMs = WAKE_RECHECK_MS, tickMs = EXPIRY_TICK_MS }: Props = $props()

  let job = $state<Status | null>(null)
  /** When `job` arrived, on the monotonic clock: the expiry countdown runs from the server's count anchored here. */
  let receivedAt = $state(0)
  /**
   * The page's single "this job is gone" signal: the poll, a save, a preview, a cut and "Delete now" all feed it, and
   * the first reason wins — the deleted screen then replaces everything, error lines included. The expiry countdown
   * never does: when it runs out the page polls once more, and only the API's 404/410 counts (gate r1).
   */
  let gone = $state<GoneReason | null>(null)
  /** Bumped to make the status poll run again after a terminal state (a cut queued, a save that left `done`). */
  let resume = $state(0)
  /**
   * The review UI stays mounted from the first `review` on — through a cut's `queued`/`running` and a failed cut — so
   * the editor is never destroyed mid-edit. A reload during a cut mounts it too: the analysis exists once `kind` is `cut`.
   */
  let reviewable = $state(false)
  /** The last of `review`/`done` seen: whether the last cut's files still match the saved plan. */
  let jobState = $state<'review' | 'done'>('review')
  /** Cuts seen finishing; Review fetches the new manifest on each. */
  let cuts = $state(0)
  // Not reactive: only onstatus reads it, to recognise the status that ends a cut.
  let cutPending = false

  function onstatus(next: Status) {
    job = next
    receivedAt = performance.now()
    const active = next.state === 'queued' || next.state === 'running'
    if (next.state === 'review' || next.state === 'done') {
      reviewable = true
      jobState = next.state
    }
    if (next.kind === 'cut' && active) {
      cutPending = true
      reviewable = true
    } else if (cutPending && !active) {
      cutPending = false
      // A cut we were following that now reads `done` — or already `review`, when an edit landed between its last
      // section and this poll (gate r2) — has replaced the ZIP: the results are the new manifest either way.
      if (next.kind === 'cut' && next.state !== 'failed') cuts++
    }
  }

  function goneWith(reason: GoneReason) {
    gone ??= reason
  }

  const fromError = (err: ApiError): GoneReason => (err.code === 'not_found' ? 'not_found' : 'expired')

  /** The poll loop has stopped: nothing but this page asks the API again. */
  const settled = $derived(!gone && job !== null && TERMINAL_STATES.has(job.state))

  /**
   * Ask the API once more (gate r2). The countdown runs on the monotonic clock and the timers on the same one, and
   * both stand still through a system suspend, so a page that wakes up has no idea how long it slept: the server's
   * fresh `seconds_left` re-anchors it, and only the server's 404/410 makes it the deleted screen. While the job is
   * still queued/running the loop is polling anyway.
   */
  function recheck() {
    if (settled) resume++
  }

  // Wake-ups that fire no event (a headless or lidless machine) are caught by the two clocks drifting apart: the
  // wall clock keeps counting through a suspend and the monotonic one does not. A gap of a minute — the countdown's
  // own resolution — counts; the wall clock still never sets the countdown, it only prompts asking the server. The
  // interval also paces the plain periodic re-check.
  $effect(() => {
    if (!settled) return
    let wall = Date.now()
    let mono = performance.now()
    let due = mono + recheckMs
    const timer = setInterval(() => {
      const w = Date.now()
      const m = performance.now()
      const slept = w - wall - (m - mono) > EXPIRY_TICK_MS
      wall = w
      mono = m
      if (slept || m >= due) {
        due = m + recheckMs
        recheck()
      }
    }, tickMs)
    return () => clearInterval(timer)
  })
</script>

<svelte:window onpageshow={recheck} ononline={recheck} />
<svelte:document onvisibilitychange={() => document.visibilityState === 'visible' && recheck()} />

{#if gone}
  <Expired reason={gone} />
{:else}
  <JobStatus {id} {load} {pollMs} {resume} {onstatus} ongone={(err) => goneWith(fromError(err))} />
  {#if job}
    <Expiry
      {id}
      secondsLeft={job.seconds_left}
      {receivedAt}
      ondeleted={() => goneWith('deleted')}
      onexpired={() => resume++}
      {...expiry}
    />
  {/if}
  {#if reviewable && job}
    <Review
      {id}
      {mode}
      pages={job.pages}
      {jobState}
      {job}
      {cuts}
      oncut={() => {
        cutPending = true
        resume++
      }}
      onsaved={() => resume++}
      ongone={(err) => goneWith(fromError(err))}
      {...review}
    />
  {/if}
  <p class="again"><a href="/" onclick={(e) => linkClick(e, '/')}>Split another PDF</a></p>
{/if}

<style>
  .again {
    margin-top: 1rem;
  }
</style>
