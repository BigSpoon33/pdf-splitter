<script lang="ts">
  import type { ComponentProps } from 'svelte'
  import { getJob, type ApiError, type JobStatus as Status } from '../lib/api'
  import { linkClick } from '../lib/route'
  import Expired, { type GoneReason } from './Expired.svelte'
  import Expiry from './Expiry.svelte'
  import JobStatus from './JobStatus.svelte'
  import Review from './Review.svelte'

  interface Props {
    id: string
    /** Everything Review, Expiry and JobStatus load, injectable so the page's state transitions are testable. */
    load?: (id: string, signal: AbortSignal) => Promise<Status>
    review?: Partial<ComponentProps<typeof Review>>
    expiry?: Partial<ComponentProps<typeof Expiry>>
    pollMs?: number
  }

  let { id, load = getJob, review = {}, expiry = {}, pollMs }: Props = $props()

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
  // Not reactive: only onstatus reads it, to recognise the `done` that ends a cut.
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
      if (next.state === 'done') cuts++
    }
  }

  function goneWith(reason: GoneReason) {
    gone ??= reason
  }

  const fromError = (err: ApiError): GoneReason => (err.code === 'not_found' ? 'not_found' : 'expired')
</script>

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
