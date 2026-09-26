<script lang="ts">
  import { untrack } from 'svelte'
  import {
    ApiError,
    getAnalysis,
    getManifest,
    getPlan,
    putPlan,
    isGone,
    type Analysis,
    type CreatedJob,
    type JobStatus,
    type ManifestRow,
    type Plan,
    type SaveOptions,
    type SectionPlan,
    type SectionPlanRequest,
    type SheetDpi,
  } from '../lib/api'
  import { MANIFEST_RETRY_MS } from '../lib/config'
  import { PlanEditor } from '../lib/editor.svelte'
  import { messageFor } from '../lib/errors'
  import { initialPicker } from '../lib/plan'
  import Download from './Download.svelte'
  import LayoutPanel from './LayoutPanel.svelte'
  import PagePreview from './PagePreview.svelte'
  import RangeEditor from './RangeEditor.svelte'
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
    loadSectionPlan?: (id: string, i: number, body: SectionPlanRequest, signal: AbortSignal) => Promise<SectionPlan>
    loadSheet?: (id: string, n: number, dpi: SheetDpi, signal: AbortSignal) => Promise<Blob>
    save?: (id: string, plan: Plan, opts?: SaveOptions) => Promise<Plan>
    cut?: (id: string) => Promise<CreatedJob>
    debounceMs?: number
    undoMs?: number
    /** How long after a failed results refresh the one automatic retry goes out. */
    retryMs?: number
    /** The latest status (the cut's progress and outcome). */
    job?: JobStatus | null
    /** Cuts the page has seen finish; each new one replaces the results with the new manifest. */
    cuts?: number
    /** A cut was queued: the page polls again to follow it. */
    oncut?: () => void
    /** A save landed while the job was `done`: the API moved it back to `review`, and the page polls once to show that. */
    onsaved?: () => void
    /** A 404/410 from a save, a preview, a cut or the manifest: the whole page becomes the deleted screen. */
    ongone?: (err: ApiError) => void
  }

  let {
    id,
    pages,
    jobState,
    loadAnalysis = getAnalysis,
    loadPlan = getPlan,
    loadManifest = getManifest,
    loadSectionPlan,
    loadSheet,
    save = putPlan,
    cut,
    debounceMs,
    undoMs,
    retryMs = MANIFEST_RETRY_MS,
    job = null,
    cuts = 0,
    oncut,
    onsaved,
    ongone,
  }: Props = $props()

  let analysis = $state<Analysis | null>(null)
  let editor = $state<PlanEditor | null>(null)
  /** A cut happened at some point (its manifest exists), so the badges and the "cut again" note apply. */
  let hasManifest = $state(false)
  /** The last cut's rows as the results list shows them: unlike `editor.rows` (badges), edits never drop one. */
  let results = $state<ManifestRow[] | null>(null)
  /** The new cut's rows could not be read: shown in place of any list, since the old rows' links no longer match. */
  let resultsError = $state<string | null>(null)
  let refreshing = $state(false)
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
        results = rows
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

  const cutting = $derived(job?.kind === 'cut' && (job.state === 'queued' || job.state === 'running'))

  /**
   * Page-range mode shows the range editor and the list; the source picker, preview and layout are chapter tools. The
   * saved plan's `source` is the ONLY thing that decides it (ADR-009 as built): the mode was fixed at upload and the
   * API wrote the first plan for it, so loading a job — from any link, with any query — never writes a plan.
   */
  const ranges = $derived(editor?.plan.source === 'ranges')

  /** The files of the last cut follow an older plan: from `review` they already do; from `done`, once an edit lands. */
  const stale = $derived(!cutting && hasManifest && (jobState === 'review' || (editor?.edited ?? false)))

  // A finished cut replaced the ZIP: its manifest is the new results list and the new badges. The old rows go first
  // (gate r1): their links now point at other files, so a refresh that fails must show an error, not them.
  let refreshed = 0
  let refresh: AbortController | null = null
  function loadResults(ed: PlanEditor, retry: boolean) {
    refresh?.abort()
    const ctrl = new AbortController()
    refresh = ctrl
    refreshing = true
    loadManifest(id, ctrl.signal).then(
      (rows) => {
        if (ctrl.signal.aborted) return
        refreshing = false
        resultsError = null
        results = rows
        ed.rows = rows
        hasManifest = true
        // Only an edit made while the cut ran (refused as `busy`) is newer than these files.
        ed.edited = ed.dirty
        if (ed.error?.code === 'busy') void ed.flush()
      },
      (err: unknown) => {
        if (ctrl.signal.aborted) return
        refreshing = false
        if (isGone(err)) {
          ongone?.(err as ApiError)
          return
        }
        resultsError = err instanceof ApiError ? err.userMessage : messageFor(null)
        if (retry) setTimeout(() => refresh === ctrl && loadResults(ed, false), retryMs)
      },
    )
  }
  $effect(() => {
    const n = cuts
    const ed = editor
    if (!ed || n <= refreshed) return
    refreshed = n
    results = null
    loadResults(ed, true)
  })
  $effect(() => () => {
    refresh?.abort()
    refresh = null
  })

  // AC-2: a save from `done` puts the job back in `review` server-side; the status card should say so.
  let seenSaves = 0
  $effect(() => {
    const n = editor?.saves ?? 0
    if (n <= seenSaves) return
    seenSaves = n
    if (untrack(() => jobState) === 'done') onsaved?.()
  })

  $effect(() => {
    if (editor?.gone) ongone?.(untrack(() => editor?.error) ?? new ApiError(410, 'expired'))
  })
</script>

{#if loadError}
  <section class="card"><p class="error" role="alert">{loadError}</p></section>
{:else if !analysis || !editor}
  <section class="card" aria-busy="true"><p class="muted">Loading the analysis…</p></section>
{:else}
  <div class="review" data-mode={ranges ? 'ranges' : 'chapters'}>
    {#if ranges}
      <section class="card">
        <RangeEditor {editor} {pages} />
        {#if editor.errorAt('source')}
          <p class="field-error" role="alert">{editor.errorAt('source')}</p>
        {/if}
      </section>

      <section class="card">
        <SectionList {editor} {analysis} ranges />
      </section>
    {:else}
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
        <PagePreview {editor} {analysis} {id} {loadSectionPlan} {loadSheet} />
      </section>

      <section class="card">
        <h2>Layout</h2>
        <LayoutPanel {editor} />
      </section>
    {/if}

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

    <section class="card">
      <Download
        {id}
        {editor}
        {job}
        {results}
        {resultsError}
        retrying={refreshing}
        {stale}
        {cut}
        {oncut}
        {ongone}
        onretry={() => editor && loadResults(editor, false)}
      />
    </section>

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
