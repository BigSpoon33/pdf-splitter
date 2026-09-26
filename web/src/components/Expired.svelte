<script lang="ts" module>
  /** `deleted`: this page's "Delete now"; `expired`: a 410 (deleted elsewhere, or past `expires_at`); `not_found`: a 404. */
  export type GoneReason = 'deleted' | 'expired' | 'not_found'
</script>

<script lang="ts">
  import { MESSAGES } from '../lib/errors'
  import { linkClick } from '../lib/route'

  let { reason }: { reason: GoneReason } = $props()

  const TEXT: Record<GoneReason, { title: string; body: string }> = {
    deleted: { title: 'Your files were deleted', body: 'The PDF and every file cut from it are gone from the server.' },
    expired: { title: 'This job was deleted', body: MESSAGES.expired },
    not_found: { title: 'There is no job here', body: MESSAGES.not_found },
  }

  let heading: HTMLHeadingElement | undefined = $state()
  // The page under the reader just vanished (a delete, a 410 mid-session): move focus to what replaced it.
  $effect(() => heading?.focus())
</script>

<section class="card gone" data-gone={reason}>
  <h1 tabindex="-1" bind:this={heading}>{TEXT[reason].title}</h1>
  <p>{TEXT[reason].body}</p>
  <p><a href="/" onclick={(e) => linkClick(e, '/')}>Split another PDF</a></p>
</section>

<style>
  h1 {
    font-size: 1.35rem;
    margin: 0 0 0.5rem;
  }
  h1:focus {
    outline: none;
  }
  p:last-child {
    margin-bottom: 0;
  }
</style>
