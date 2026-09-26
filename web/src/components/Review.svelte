<script lang="ts">
  import {
    ApiError,
    getAnalysis,
    getManifest,
    getPlan,
    putPlan,
    type Analysis,
    type ManifestRow,
    type Plan,
    type SaveOptions,
  } from '../lib/api'
  import { PlanEditor } from '../lib/editor.svelte'
  import { messageFor } from '../lib/errors'
  import { initialPicker } from '../lib/plan'
  import LayoutPanel from './LayoutPanel.svelte'
  import SectionList from './SectionList.svelte'
  import SourcePicker from './SourcePicker.svelte'

  interface Props {
    id: string
    pages: number
    /** `done`: the last cut matches the saved plan; `review`: no cut yet, or the plan changed since one. */
    jobState: 'review' | 'done'
    loadAnalysis?: (id: string, signal: AbortSignal) => Promise<Analysis>
    loadPlan?: (id: string, signal: AbortSignal) => Promise<Plan>
    loadManifest?: (id: string, signal: AbortSignal) => Promise<ManifestRow[]>
    save?: (id: string, plan: Plan, opts?: SaveOptions) => Promise<Plan>
    debounceMs?: number
    undoMs?: number
  }

  let {
    id,
    pages,
    jobState,
    loadAnalysis = getAnalysis,
    loadPlan = getPlan,
    loadManifest = getManifest,
    save = putPlan,
    debounceMs,
    undoMs,
  }: Props = $props()

  let analysis = $state<Analysis | null>(null)
  let editor = $state<PlanEditor | null>(null)
  /** A cut happened at some point (its manifest exists), so the badges and the "cut again" note apply. */
  let hasManifest = $state(false)
  let loadError = $state<string | null>(null)

  $effect(() => {
    const ctrl = new AbortController()
    let created: PlanEditor | null = null
    // Badges are a bonus: a manifest that can't be read (409 before any cut, a hiccup) just means no badges.
    const manifest = loadManifest(id, ctrl.signal).then(
      (rows) => rows,
      () => null,
    )
    void (async () => {
      try {
        const [a, plan] = await Promise.all([loadAnalysis(id, ctrl.signal), loadPlan(id, ctrl.signal)])
        if (ctrl.signal.aborted) return
        analysis = a
        created = new PlanEditor(plan, (p, o) => save(id, p, o), { debounceMs, undoMs, picker: initialPicker(a) })
        editor = created
        const rows = await manifest
        if (ctrl.signal.aborted || !rows) return
        created.rows = rows
        hasManifest = true
      } catch (err) {
        if (ctrl.signal.aborted) return
        loadError = err instanceof ApiError ? err.userMessage : messageFor(null)
      }
    })()
    return () => {
      ctrl.abort()
      created?.destroy()
    }
  })

  const status = $derived.by(() => {
    if (!editor) return null
    if (editor.error) return null
    if (editor.saving) return 'Saving…'
    if (editor.dirty) return 'Unsaved changes'
    return 'Saved'
  })

  /** The files of the last cut follow an older plan: from `review` they already do; from `done`, once an edit lands. */
  const stale = $derived(hasManifest && (jobState === 'review' || (editor?.edited ?? false)))
</script>

{#if loadError}
  <section class="card"><p class="error" role="alert">{loadError}</p></section>
{:else if !analysis || !editor}
  <section class="card" aria-busy="true"><p class="muted">Loading the analysis…</p></section>
{:else}
  <div class="review">
    <section class="card">
      <h2>Sections</h2>
      <SourcePicker
        {analysis}
        {pages}
        source={editor.plan.source}
        sections={editor.plan.sections}
        picker={editor.picker}
        settings={editor.plan.settings}
        onpick={(source, sections, label, picker) => editor?.replaceSections(source, sections, label, picker)}
      />
      {#if editor.errorAt('source')}
        <p class="field-error" role="alert">{editor.errorAt('source')}</p>
      {/if}
    </section>

    <section class="card">
      <SectionList {editor} {analysis} />
    </section>

    <section class="card">
      <h2>Layout</h2>
      <LayoutPanel {editor} />
    </section>

    <p class="save-state" role="status" aria-live="polite">
      {#if editor.error}
        <span class="error-inline">{editor.error.userMessage}</span>
        {#if !editor.gone}
          <button type="button" onclick={() => void editor?.flush()}>Retry</button>
        {/if}
      {:else if status}
        {status}
      {/if}
      {#if stale}
        <span class="muted">Your edits replace the plan of the last cut; cut again to refresh the files.</span>
      {/if}
    </p>

    {#if editor.undo}
      <div class="toast" role="status">
        <span>Section list replaced from {editor.undo.label}.</span>
        <button type="button" onclick={() => editor?.undoLast()}>Undo</button>
      </div>
    {/if}
  </div>
{/if}

<style>
  .review {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    margin-top: 1rem;
  }
  h2 {
    font-size: 1.1rem;
    margin: 0 0 0.75rem;
  }
  .muted {
    color: var(--muted);
  }
  .save-state {
    margin: 0;
    color: var(--muted);
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.75rem;
  }
  .error-inline {
    color: var(--danger);
  }
</style>
