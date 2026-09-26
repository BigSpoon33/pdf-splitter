<script lang="ts">
  import { ApiError, deleteJob, isGone } from '../lib/api'
  import { EXPIRY_TICK_MS } from '../lib/config'
  import { messageFor } from '../lib/errors'
  import { timeLeft } from '../lib/expiry'

  interface Props {
    id: string
    /** The status's `expires_at` (ISO). */
    expiresAt: string
    /** The job is gone: deleted here, or found already gone by the DELETE. */
    ondeleted: () => void
    /** The clock passed `expiresAt`: the janitor may have removed the files, and the API answers 410 from now on. */
    onexpired: () => void
    remove?: (id: string) => Promise<void>
    confirmDelete?: (message: string) => boolean
    now?: () => number
    tickMs?: number
  }

  let {
    id,
    expiresAt,
    ondeleted,
    onexpired,
    remove = deleteJob,
    confirmDelete = (message) => window.confirm(message),
    now = Date.now,
    tickMs = EXPIRY_TICK_MS,
  }: Props = $props()

  let clock = $state(Date.now())
  let deleting = $state(false)
  let error = $state<string | null>(null)

  $effect(() => {
    clock = now()
    const timer = setInterval(() => (clock = now()), tickMs)
    return () => clearInterval(timer)
  })

  const left = $derived(timeLeft(expiresAt, clock))

  $effect(() => {
    if (left === null) onexpired()
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

{#if left !== null}
  <div class="expiry">
    <p>Files deleted in {left}.</p>
    <button type="button" class="danger" onclick={() => void deleteNow()} disabled={deleting}>
      {deleting ? 'Deleting…' : 'Delete now'}
    </button>
  </div>
  {#if error}
    <p class="error" role="alert">{error}</p>
  {/if}
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
