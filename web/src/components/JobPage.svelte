<script lang="ts">
  import type { JobStatus as Status } from '../lib/api'
  import JobStatus from './JobStatus.svelte'
  import Review from './Review.svelte'

  let { id }: { id: string } = $props()

  let job = $state<Status | null>(null)
  // The analysis and plan exist from `review` on; the poll has stopped by then, so mounting here never races it.
  const ready = $derived(job !== null && (job.state === 'review' || job.state === 'done') ? job.state : null)
</script>

<JobStatus {id} onstatus={(next) => (job = next)} />
{#if ready && job}
  <Review {id} pages={job.pages} jobState={ready} />
{/if}
