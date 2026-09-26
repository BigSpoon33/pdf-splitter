<script lang="ts">
  import { ApiError, deleteJob, isGone } from '../lib/api'
  import { EXPIRY_GRACE_MS, EXPIRY_TICK_MS } from '../lib/config'
  import { messageFor } from '../lib/errors'
  import { timeLeft } from '../lib/expiry'

  interface Props {
    id: string
    /** The status's `seconds_left`: the server's own count, so a visitor's clock that is hours off changes nothing. */
    secondsLeft: number
    /** When that status arrived, on the monotonic clock (`now()`); the countdown runs from there. */
    receivedAt: number
    /** The job is gone: deleted here, or found already gone by the DELETE. */
    ondeleted: () => void
    /** The server's count ran out: ask it again — only its 410 makes the job gone. */
    onexpired: () => void
    remove?: (id: string) => Promise<void>
    confirmDelete?: (message: string) => boolean
    now?: () => number
    tickMs?: number
    graceMs?: number
  }

  let {
    id,
    secondsLeft,
    receivedAt,
    ondeleted,
    onexpired,
    remove = deleteJob,
    confirmDelete = (message) => window.confirm(message),
    now = () => performance.now(),
    tickMs = EXPIRY_TICK_MS,
    graceMs = EXPIRY_GRACE_MS,
  }: Props = $props()

  let clock = $state(0)
  let deleting = $state(false)
  let error = $state<string | null>(null)

  $effect(() => {
    // Re-read at every status too: a re-check after a suspend (gate r2) must show the server's figure at once, not
    // one that a tick up to a minute old skews.
    void receivedAt
    clock = now()
    const timer = setInterval(() => (clock = now()), tickMs)
    return () => clearInterval(timer)
  })

  // Whole seconds elapsed, like the server's count: the milliseconds between the answer and this render would
  // otherwise floor a fresh "1 h" to "59 min".
  const left = $derived(timeLeft((secondsLeft - Math.floor(Math.max(clock - receivedAt, 0) / 1000)) * 1000))

  // Re-armed by every status: when the server's count runs out, one more poll settles it (a 410, or a fresh count).
  $effect(() => {
    const due = secondsLeft * 1000 - (now() - receivedAt) + graceMs
    const timer = setTimeout(() => onexpired(), Math.max(due, 0))
    return () => clearTimeout(timer)
  })

  async function deleteNow() {
    if (deleting || !confirmDelete('Delete your PDF and every file cut from it now? This cannot be undone.')) return
    deleting = true
    error = null
    try {
      await remove(id)
      ondeleted()
    } catch (err) {
      if (isGone(err)) ondeleted()
      else error = err instanceof ApiError ? err.userMessage : messageFor(null)
    } finally {
      deleting = false
    }
  }
</script>

<div class="expiry">
  <p>Files deleted in {left}.</p>
  <button type="button" class="danger" onclick={() => void deleteNow()} disabled={deleting}>
    {deleting ? 'Deleting…' : 'Delete now'}
  </button>
</div>
{#if error}
  <p class="error" role="alert">{error}</p>
{/if}

<style>
  .expiry {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem 1rem;
    margin-top: 0.75rem;
    color: var(--muted);
  }
  .expiry p {
    margin: 0;
  }
  .danger {
    color: var(--danger);
    border-color: color-mix(in srgb, var(--danger) 45%, var(--border));
  }
</style>
