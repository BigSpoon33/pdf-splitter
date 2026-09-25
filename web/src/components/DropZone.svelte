<script lang="ts" module>
  import type { ErrorCode } from '../lib/errors'

  /** The client-side precheck: the API repeats both checks (and reads the `%PDF-` magic), this only spares a doomed upload. */
  export function precheck(file: File, maxBytes: number): ErrorCode | null {
    const looksPdf = file.name.toLowerCase().endsWith('.pdf') || file.type === 'application/pdf'
    if (!looksPdf || file.size === 0) return 'not_pdf'
    if (file.size > maxBytes) return 'too_large'
    return null
  }
</script>

<script lang="ts">
  import { ApiError, createJob, type CreatedJob } from '../lib/api'
  import { MAX_BYTES } from '../lib/config'
  import { messageFor } from '../lib/errors'

  interface Props {
    oncreated: (id: string) => void
    maxBytes?: number
    upload?: (file: File, onProgress: (fraction: number) => void) => Promise<CreatedJob>
  }

  let { oncreated, maxBytes = MAX_BYTES, upload = createJob }: Props = $props()

  let dragging = $state(false)
  let error = $state<string | null>(null)
  let progress = $state<number | null>(null)
  let input: HTMLInputElement | undefined = $state()

  const uploading = $derived(progress !== null)
  const limitMb = $derived(Math.round(maxBytes / (1024 * 1024)))

  async function start(file: File | undefined) {
    if (!file || uploading) return
    error = null
    const rejected = precheck(file, maxBytes)
    if (rejected) {
      error = messageFor(rejected)
      return
    }
    progress = 0
    try {
      const job = await upload(file, (f) => (progress = f))
      oncreated(job.id)
    } catch (err) {
      error = err instanceof ApiError ? err.userMessage : messageFor(null)
    } finally {
      progress = null
      // Picking the same file again after an error must fire `change` again.
      if (input) input.value = ''
    }
  }

  function onDragOver(e: DragEvent) {
    e.preventDefault()
    if (e.dataTransfer) e.dataTransfer.dropEffect = uploading ? 'none' : 'copy'
    dragging = true
  }

  function onDrop(e: DragEvent) {
    e.preventDefault()
    dragging = false
    void start(e.dataTransfer?.files[0])
  }
</script>

<section class="card">
  <label
    class="zone"
    class:dragging
    class:busy={uploading}
    ondragenter={onDragOver}
    ondragover={onDragOver}
    ondragleave={() => (dragging = false)}
    ondrop={onDrop}
    data-testid="dropzone"
  >
    <input
      bind:this={input}
      class="visually-hidden"
      type="file"
      accept=".pdf,application/pdf"
      disabled={uploading}
      onchange={(e) => start(e.currentTarget.files?.[0])}
    />
    {#if uploading}
      <span class="title">Uploading… {Math.round((progress ?? 0) * 100)}%</span>
      <span
        class="bar"
        role="progressbar"
        aria-label="Upload progress"
        aria-valuemin="0"
        aria-valuemax="100"
        aria-valuenow={Math.round((progress ?? 0) * 100)}
      >
        <span style:width="{(progress ?? 0) * 100}%"></span>
      </span>
    {:else}
      <span class="title">Drop a PDF here</span>
      <span class="hint">or <span class="pick">choose a file</span> · PDF with a text layer, up to {limitMb} MB</span>
    {/if}
  </label>
  {#if error}
    <p class="error" role="alert">{error}</p>
  {/if}
</section>

<style>
  .zone {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.75rem;
    min-height: 12rem;
    padding: 1.5rem 1rem;
    border: 2px dashed var(--border);
    border-radius: var(--radius);
    text-align: center;
    cursor: pointer;
    transition:
      border-color 0.15s,
      background 0.15s;
  }
  .zone:hover,
  .zone.dragging {
    border-color: var(--accent);
    background: var(--accent-soft);
  }
  .zone:focus-within {
    box-shadow: var(--focus);
  }
  .zone.busy {
    cursor: progress;
  }
  .title {
    font-size: 1.15rem;
    font-weight: 600;
  }
  .hint {
    color: var(--muted);
  }
  .pick {
    color: var(--accent);
    text-decoration: underline;
  }
  .bar {
    width: min(20rem, 100%);
  }
</style>
