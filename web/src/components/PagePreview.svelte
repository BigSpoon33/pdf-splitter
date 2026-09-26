<script lang="ts">
  /**
   * The selected section's first and last sheet with the engine's plan drawn over them (AC-1), and the lines the
   * visitor can drag or nudge: the start/end cuts (an override on this section, AC-2/AC-5), the gutter and the
   * header/footer bands (settings for the whole book, AC-3). The overlay is an SVG in page POINTS (`viewBox`
   * = the page), scaled with the image, so nothing here converts to pixels; `geometry.ts` maps the pointer back.
   *
   * The engine is asked again (`POST /sections/{i}/plan`, nothing persisted) when the selection changes, when a
   * drag is released, and when a save lands — the route plans against the SAVED section list, so a delete or an
   * insert shows up once the server has it. Requests that would repeat the last one are skipped: a drag's own
   * request already covers the save that follows it, and a settings change costs a re-index on the server.
   */
  import { ApiError, getSectionPlan, getSheet, isGone, type Analysis, type Col, type Cut, type Override, type SectionPlan, type SectionPlanRequest, type SheetDpi } from '../lib/api'
  import { PREVIEW_DPI } from '../lib/config'
  import type { PlanEditor } from '../lib/editor.svelte'
  import { clamp, colAt, colSpan, frameOf, gutterX, halfPoint, nudgeFor, pointerToPoint } from '../lib/geometry'
  import { flagLabel } from '../lib/plan'
  import { untrack } from 'svelte'

  type Which = 'start' | 'end'
  type Handle = Which | 'gutter' | 'header' | 'footer'

  interface Props {
    editor: PlanEditor
    analysis: Analysis
    id: string
    loadSectionPlan?: (id: string, i: number, body: SectionPlanRequest, signal: AbortSignal) => Promise<SectionPlan>
    loadSheet?: (id: string, n: number, dpi: SheetDpi, signal: AbortSignal) => Promise<Blob>
    dpi?: SheetDpi
  }

  let { editor, analysis, id, loadSectionPlan = getSectionPlan, loadSheet = getSheet, dpi = PREVIEW_DPI }: Props = $props()

  const MAX_BAND = 200
  const SPLIT_MIN = 0.2
  const SPLIT_MAX = 0.8

  let view = $state<SectionPlan | null>(null)
  let loading = $state(false)
  let viewError = $state<ApiError | null>(null)
  let sheetError = $state<ApiError | null>(null)
  /** Object URLs of the sheets on show, by 1-based sheet. */
  let sheetUrls = $state<Record<number, string>>({})
  /** A line being dragged: its live value in points, and for a cut the column the drag started in. */
  let drag = $state<{ kind: Handle; value: number; col: Col } | null>(null)
  /** Bumped when a release wants the engine's view now rather than after the save. */
  let refresh = $state(0)

  let lastKey: string | null = null
  /** The section `view` describes: another section's sheets must not stay up while its own request is out. */
  let viewIndex: number | null = null
  let ctrl: AbortController | null = null
  const pendingSheets = new Map<number, AbortController>()

  const selected = $derived(editor.selected)
  const settings = $derived(editor.plan.settings)
  const override = $derived(selected === null ? undefined : editor.plan.overrides[String(selected)])
  const section = $derived(selected === null ? undefined : editor.plan.sections[selected])

  function requestFor(i: number): SectionPlanRequest {
    const plan = $state.snapshot(editor.plan)
    // The local plan is the truth the visitor sees: its settings, and its override for this section (`null` =
    // the engine's own plan, which is what an absent key means — a key sent as absent would mean "the saved one").
    return { settings: plan.settings, override: plan.overrides[String(i)] ?? null }
  }

  function fail(err: unknown): ApiError {
    const apiErr = err instanceof ApiError ? err : new ApiError(0, 'network')
    if (isGone(apiErr)) {
      // A preview answers 410 when the job was deleted during the render: the whole page is over, not just this one.
      editor.gone = true
      editor.error = apiErr
    }
    return apiErr
  }

  $effect(() => {
    const i = selected
    void editor.saves
    void refresh
    if (i === null) {
      untrack(() => {
        ctrl?.abort()
        ctrl = null
        view = null
        viewError = null
        loading = false
        lastKey = null
      })
      return
    }
    untrack(() => {
      const body = requestFor(i)
      const key = JSON.stringify([i, editor.plan.sections.map((s) => [s.page, s.heading]), body])
      if (key === lastKey) return
      lastKey = key
      ctrl?.abort()
      const mine = new AbortController()
      ctrl = mine
      loading = true
      viewError = null
      if (viewIndex !== i) view = null
      loadSectionPlan(id, i, body, mine.signal).then(
        (v) => {
          if (mine.signal.aborted) return
          view = v
          viewIndex = i
          loading = false
        },
        (err: unknown) => {
          if (mine.signal.aborted) return
          viewError = fail(err)
          loading = false
          lastKey = null
        },
      )
    })
  })

  $effect(() => {
    const wanted = view ? [...new Set(view.pages)] : []
    untrack(() => {
      for (const n of wanted) if (!(n in sheetUrls) && !pendingSheets.has(n)) loadOne(n)
      for (const [n, c] of pendingSheets) if (!wanted.includes(n)) (c.abort(), pendingSheets.delete(n))
      for (const key of Object.keys(sheetUrls)) if (!wanted.includes(Number(key))) dropUrl(Number(key))
    })
  })

  function loadOne(n: number): void {
    const c = new AbortController()
    pendingSheets.set(n, c)
    loadSheet(id, n, dpi, c.signal).then(
      (blob) => {
        if (c.signal.aborted) return
        pendingSheets.delete(n)
        sheetUrls[n] = URL.createObjectURL(blob)
      },
      (err: unknown) => {
        if (c.signal.aborted) return
        pendingSheets.delete(n)
        sheetError = fail(err)
      },
    )
  }

  function dropUrl(n: number): void {
    const url = sheetUrls[n]
    if (url) URL.revokeObjectURL(url)
    delete sheetUrls[n]
  }

  $effect(() => () => {
    ctrl?.abort()
    for (const c of pendingSheets.values()) c.abort()
    for (const key of Object.keys(sheetUrls)) dropUrl(Number(key))
  })

  function retry(): void {
    sheetError = null
    lastKey = null
    refresh++
    if (view) for (const n of new Set(view.pages)) if (!(n in sheetUrls) && !pendingSheets.has(n)) loadOne(n)
  }

  // ── What the overlay shows: the drag while it lasts, else the override (the local truth), else the engine ──

  function cutOf(which: Which): { y: Cut; col: Col } {
    if (drag?.kind === which) return { y: drag.value, col: drag.col }
    const cutKey = `${which}Cut` as const
    const colKey = `${which}Col` as const
    const y = override && cutKey in override ? (override[cutKey] ?? null) : (view?.[cutKey] ?? null)
    const col = override?.[colKey] ?? view?.[colKey] ?? 'full'
    return { y, col }
  }

  const split = $derived(drag?.kind === 'gutter' ? drag.value : gutterX(settings, W()))
  const headerY = $derived(drag?.kind === 'header' ? drag.value : settings.header_band)
  const footerY = $derived(drag?.kind === 'footer' ? drag.value : settings.footer_band)

  /** The first sheet's size stands for the section: the engine plans every cut in it (`Book.page_size(sheet0)`). */
  function sizeOf(n: number): { W: number; H: number } {
    return analysis.size[n - 1] ?? analysis.size[0] ?? { W: 1, H: 1 }
  }
  function W(): number {
    return sizeOf(view?.pages[0] ?? section?.page ?? 1).W
  }
  function H(): number {
    return sizeOf(view?.pages[0] ?? section?.page ?? 1).H
  }

  function rectsOn(n: number): [number, number, number, number][] {
    return (view?.rects ?? []).filter(([sheet]) => sheet === n).map(([, r]) => r)
  }

  // ── Dragging (AC-2/AC-3): the overlay follows the pointer; the release edits the plan once ──

  function pointAt(e: PointerEvent): { x: number; y: number } {
    const svg = (e.currentTarget as Element).closest('svg')
    const box = svg?.getBoundingClientRect() ?? { left: 0, top: 0, width: 0, height: 0 }
    return pointerToPoint(frameOf({ W: W(), H: H() }, dpi), { x: e.clientX, y: e.clientY }, box)
  }

  function valueFor(kind: Handle, p: { x: number; y: number }): number {
    switch (kind) {
      case 'gutter':
        return clamp(p.x, SPLIT_MIN * W(), SPLIT_MAX * W())
      case 'header':
        return clamp(halfPoint(p.y), 0, MAX_BAND)
      case 'footer':
        return clamp(halfPoint(H() - p.y), 0, MAX_BAND)
      default:
        return clamp(halfPoint(p.y), 0, H())
    }
  }

  function isCut(kind: Handle): kind is Which {
    return kind === 'start' || kind === 'end'
  }

  function onDown(kind: Handle) {
    return (e: PointerEvent) => {
      if (e.button !== 0 || editor.gone) return
      e.preventDefault()
      const p = pointAt(e)
      // AC-2: the column the drag STARTS in is the cut's column; a one-column book has only full-width cuts.
      const col = isCut(kind) ? colAt(p.x, W(), settings) : 'full'
      drag = { kind, value: valueFor(kind, p), col }
      ;(e.currentTarget as Element).setPointerCapture?.(e.pointerId)
    }
  }

  function onMove(e: PointerEvent): void {
    if (!drag) return
    drag.value = valueFor(drag.kind, pointAt(e))
  }

  function onUp(): void {
    if (!drag) return
    const { kind, value, col } = drag
    drag = null
    commit(kind, value, col)
    refresh++
  }

  /** One edit per release or keystroke; the editor's debounce makes a run of them one save. */
  function commit(kind: Handle, value: number, col: Col): void {
    if (selected === null) return
    switch (kind) {
      case 'gutter':
        editor.setSetting('column_split', Math.round((value / W()) * 1000) / 1000)
        break
      case 'header':
        editor.setSetting('header_band', value)
        break
      case 'footer':
        editor.setSetting('footer_band', value)
        break
      default:
        editor.setOverride(selected, { ...override, [`${kind}Cut`]: value, [`${kind}Col`]: col } as Override)
    }
  }

  // ── Keyboard (AC-5): the focused line moves 1 pt per arrow, 10 with Shift; the save's landing refreshes the view ──

  function onKey(kind: Handle) {
    return (e: KeyboardEvent) => {
      const delta = nudgeFor(e.key, e.shiftKey, kind === 'gutter' ? 'horizontal' : 'vertical')
      if (delta === 0 || editor.gone) return
      e.preventDefault()
      if (isCut(kind)) {
        const cut = cutOf(kind)
        const y = (cut.y ?? (kind === 'start' ? 0 : H())) + delta
        commit(kind, valueFor(kind, { x: 0, y }), cut.col)
        return
      }
      // The footer band is measured from the bottom, so the key that moves its edge up makes it bigger.
      const p = kind === 'gutter' ? { x: split + delta, y: 0 } : { x: 0, y: (kind === 'header' ? headerY : H() - footerY) + delta }
      commit(kind, valueFor(kind, p), 'full')
    }
  }

  function removeCut(which: Which): void {
    if (selected === null) return
    // `null` is an instruction to the engine (drop the cut it found); an absent key would keep it.
    editor.setOverride(selected, { ...override, [`${which}Cut`]: null } as Override)
    refresh++
  }

  function reset(): void {
    if (selected === null) return
    editor.setOverride(selected, null)
    refresh++
  }

  const fmt = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1))
  const colName = (col: Col) => (col === 'full' ? 'full width' : `${col} column`)
</script>

{#snippet cutLine(which: Which, cut: { y: Cut; col: Col }, size: { W: number; H: number })}
  {@const y = cut.y ?? (which === 'start' ? 0 : size.H)}
  {@const [x0, x1] = colSpan(cut.col, size.W, split)}
  <line class="cut" class:absent={cut.y === null} x1={x0} y1={y} x2={x1} y2={y} />
  <!-- The grip spans the page whatever column the line is in: where the drag starts decides the column (AC-2);
       kept inside the page so a line at its very edge (an absent cut) can still be grabbed. -->
  <rect
    class="grip horizontal"
    x="0"
    y={clamp(y - 8, 0, size.H - 16)}
    width={size.W}
    height="16"
    role="slider"
    tabindex="0"
    aria-label="{which === 'start' ? 'Start' : 'End'} cut of section {(selected ?? 0) + 1}"
    aria-orientation="vertical"
    aria-valuemin="0"
    aria-valuemax={size.H}
    aria-valuenow={y}
    aria-valuetext={cut.y === null ? `no ${which} cut (drag or press an arrow key to add one)` : `${fmt(cut.y)} pt, ${colName(cut.col)}`}
    onpointerdown={onDown(which)}
    onkeydown={onKey(which)}
  />
  <text class="label" x={which === 'start' ? x0 + 4 : x1 - 4} y={which === 'start' ? y + 11 : y - 4} text-anchor={which === 'start' ? 'start' : 'end'}>
    {cut.y === null ? `no ${which} cut` : `${which} ${fmt(cut.y)} · ${colName(cut.col)}`}
  </text>
{/snippet}

{#if selected === null || !section}
  <p class="muted">Select a section to preview where it will be cut.</p>
{:else}
  {@const pages = view?.pages ?? null}
  {@const sheets = pages ? [...new Set(pages)] : []}
  <div class="preview" data-testid="page-preview">
    <div class="head">
      <h2>
        Section {selected + 1}: {section.name}
        {#if pages}
          <span class="muted">— {pages[0] === pages[1] ? `sheet ${pages[0]}` : `sheets ${pages[0]}–${pages[1]}`}</span>
        {/if}
      </h2>
      {#if override}
        <button type="button" onclick={reset}>Reset cut</button>
      {/if}
    </div>
    {#if view?.flags.length}
      <ul class="flags" aria-label="Flags of the planned cut">
        {#each view.flags as flag (flag)}
          <li class="badge" class:info={flag === 'override'}>{flagLabel(flag)}</li>
        {/each}
      </ul>
    {/if}
    {#if viewError || sheetError}
      {@const err = viewError ?? sheetError}
      <p class="error" role="alert">
        {err?.userMessage}
        {#if !editor.gone}
          <button type="button" onclick={retry}>Retry</button>
        {/if}
      </p>
    {:else if !view}
      <p class="muted" aria-busy="true">Loading the preview…</p>
    {/if}

    {#if view && pages}
      {@const first = pages[0]}
      {@const last = pages[1]}
      {@const size = sizeOf(first)}
      <div class="sheets" class:updating={loading} aria-busy={loading}>
        {#each sheets as n (n)}
          {@const start = n === first ? cutOf('start') : null}
          {@const end = n === last ? cutOf('end') : null}
          <figure class="sheet">
            <figcaption>
              {#if first === last}Only sheet {n}{:else if n === first}First sheet {n}{:else}Last sheet {n}{/if}
            </figcaption>
            <div class="frame" style="aspect-ratio: {size.W} / {size.H}">
              {#if sheetUrls[n]}
                <img src={sheetUrls[n]} alt="Sheet {n}" draggable="false" />
              {/if}
              <!-- svelte-ignore a11y_no_static_element_interactions -->
              <svg viewBox="0 0 {size.W} {size.H}" preserveAspectRatio="none" aria-label="Cut plan on sheet {n}" onpointermove={onMove} onpointerup={onUp} onpointercancel={onUp}>
                <defs>
                  <pattern id="hatch-{n}" patternUnits="userSpaceOnUse" width="8" height="8" patternTransform="rotate(45)">
                    <line x1="0" y1="0" x2="0" y2="8" class="hatch" />
                  </pattern>
                </defs>
                <rect class="band" x="0" y="0" width={size.W} height={headerY} />
                <rect class="band" x="0" y={size.H - footerY} width={size.W} height={footerY} />
                {#each rectsOn(n) as [x0, y0, x1, y1], k (k)}
                  <rect class="removed" x={x0} y={y0} width={Math.max(0, x1 - x0)} height={Math.max(0, y1 - y0)} fill="url(#hatch-{n})" data-testid="removed" />
                {/each}

                <!-- Bands and gutter are book-wide, so they are draggable on every sheet shown. -->
                <line class="band-edge" x1="0" y1={headerY} x2={size.W} y2={headerY} />
                <rect
                  class="grip horizontal"
                  x="0"
                  y={headerY - 6}
                  width={size.W}
                  height="12"
                  role="slider"
                  tabindex="0"
                  aria-label="Header band"
                  aria-orientation="vertical"
                  aria-valuemin="0"
                  aria-valuemax={MAX_BAND}
                  aria-valuenow={headerY}
                  aria-valuetext="{fmt(headerY)} pt from the top"
                  onpointerdown={onDown('header')}
                  onkeydown={onKey('header')}
                />
                <line class="band-edge" x1="0" y1={size.H - footerY} x2={size.W} y2={size.H - footerY} />
                <rect
                  class="grip horizontal"
                  x="0"
                  y={size.H - footerY - 6}
                  width={size.W}
                  height="12"
                  role="slider"
                  tabindex="0"
                  aria-label="Footer band"
                  aria-orientation="vertical"
                  aria-valuemin="0"
                  aria-valuemax={MAX_BAND}
                  aria-valuenow={footerY}
                  aria-valuetext="{fmt(footerY)} pt from the bottom"
                  onpointerdown={onDown('footer')}
                  onkeydown={onKey('footer')}
                />
                {#if !settings.single_column}
                  <line class="gutter" x1={split} y1="0" x2={split} y2={size.H} />
                  <rect
                    class="grip vertical"
                    x={split - 6}
                    y="0"
                    width="12"
                    height={size.H}
                    role="slider"
                    tabindex="0"
                    aria-label="Gutter"
                    aria-orientation="horizontal"
                    aria-valuemin={Math.round(SPLIT_MIN * size.W)}
                    aria-valuemax={Math.round(SPLIT_MAX * size.W)}
                    aria-valuenow={Math.round(split)}
                    aria-valuetext="{fmt(halfPoint(split))} pt from the left ({Math.round((split / size.W) * 100)} %)"
                    onpointerdown={onDown('gutter')}
                    onkeydown={onKey('gutter')}
                  />
                {/if}

                {#if start}
                  {@render cutLine('start', start, size)}
                {/if}
                {#if end}
                  {@render cutLine('end', end, size)}
                {/if}
              </svg>
            </div>
            <div class="sheet-actions">
              {#if start && start.y !== null}
                <button type="button" onclick={() => removeCut('start')}>Remove start cut</button>
              {/if}
              {#if end && end.y !== null}
                <button type="button" onclick={() => removeCut('end')}>Remove end cut</button>
              {/if}
            </div>
          </figure>
        {/each}
      </div>
      <p class="legend muted">
        Hatched: removed by the cut · red line: drag it to move the cut (arrow keys nudge 1 pt, Shift 10 pt) · dashed: the gutter · shaded: header and footer bands
      </p>
    {/if}
  </div>
{/if}

<style>
  .muted {
    color: var(--muted);
    margin: 0;
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.5rem;
  }
  h2 {
    font-size: 1.1rem;
    margin: 0 0 0.5rem;
  }
  .flags {
    list-style: none;
    margin: 0 0 0.5rem;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }
  .error button {
    margin-left: 0.5rem;
  }
  .sheets {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
    gap: 1rem;
    transition: opacity 0.2s ease;
  }
  .sheets.updating {
    opacity: 0.7;
  }
  .sheet {
    margin: 0;
    min-width: 0;
  }
  figcaption {
    font-size: 0.9rem;
    color: var(--muted);
    margin-bottom: 0.3rem;
  }
  .frame {
    position: relative;
    width: 100%;
    background: var(--surface);
    border: 1px solid var(--border);
    overflow: hidden;
  }
  .frame img,
  .frame svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    display: block;
  }
  .frame img {
    user-select: none;
    -webkit-user-drag: none;
  }
  .frame svg {
    touch-action: none;
  }
  .hatch {
    stroke: var(--danger);
    stroke-width: 3;
  }
  .removed {
    stroke: var(--danger);
    stroke-width: 1;
    vector-effect: non-scaling-stroke;
    opacity: 0.55;
  }
  .band {
    fill: var(--fg);
    opacity: 0.08;
  }
  .band-edge {
    stroke: var(--muted);
    stroke-width: 1;
    stroke-dasharray: 2 3;
    vector-effect: non-scaling-stroke;
  }
  .gutter {
    stroke: var(--accent);
    stroke-width: 1;
    stroke-dasharray: 4 4;
    vector-effect: non-scaling-stroke;
  }
  .cut {
    stroke: var(--danger);
    stroke-width: 2.5;
    vector-effect: non-scaling-stroke;
  }
  .cut.absent {
    stroke-dasharray: 6 4;
    opacity: 0.6;
  }
  .grip {
    fill: transparent;
    outline: none;
  }
  .grip.horizontal {
    cursor: ns-resize;
  }
  .grip.vertical {
    cursor: ew-resize;
  }
  .grip:focus-visible {
    fill: var(--accent);
    fill-opacity: 0.25;
  }
  .label {
    font-size: 9px;
    font-weight: 700;
    fill: var(--danger);
    paint-order: stroke;
    stroke: var(--surface);
    stroke-width: 3px;
    stroke-linejoin: round;
    pointer-events: none;
  }
  .sheet-actions {
    display: flex;
    gap: 0.4rem;
    margin-top: 0.4rem;
  }
  .legend {
    font-size: 0.85rem;
    margin-top: 0.75rem;
  }
</style>
