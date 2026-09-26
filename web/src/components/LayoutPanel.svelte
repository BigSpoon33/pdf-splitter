<script lang="ts">
  import type { PlanEditor } from '../lib/editor.svelte'

  let { editor }: { editor: PlanEditor } = $props()

  const settings = $derived(editor.plan.settings)
  /** The engine's own wrap gap (`monograph_splitter.profile.Profile.heading_wrap_gap`), shown until the user sets one. */
  const ENGINE_WRAP_GAP = 16

  function numberOf(e: Event): number | null {
    const v = (e.currentTarget as HTMLInputElement).valueAsNumber
    // A blank or half-typed field is not a setting; the API would refuse NaN anyway.
    return Number.isFinite(v) ? v : null
  }

  function setNumber(key: 'column_split' | 'header_band' | 'footer_band' | 'heading_min_size' | 'heading_wrap_gap', scale = 1) {
    return (e: Event) => {
      const v = numberOf(e)
      if (v !== null) editor.setSetting(key, v * scale)
    }
  }

  const gutterPct = $derived(Math.round(settings.column_split * 1000) / 10)
</script>

<div class="panel">
  <fieldset class="columns">
    <legend>Columns</legend>
    <label>
      <input type="radio" name="columns" value="one" checked={settings.single_column} onchange={() => editor.setSetting('single_column', true)} />
      One column
    </label>
    <label>
      <input type="radio" name="columns" value="two" checked={!settings.single_column} onchange={() => editor.setSetting('single_column', false)} />
      Two columns
    </label>
  </fieldset>

  {#snippet numberField(
    key: 'column_split' | 'header_band' | 'footer_band' | 'heading_min_size' | 'heading_wrap_gap',
    text: string,
    value: number,
    min: number,
    max: number,
    step: number,
    scale: number,
    disabled: boolean,
    hint: string,
  )}
    {@const err = editor.errorAt('settings', key)}
    <div class="field">
      <label for="setting-{key}">{text}</label>
      <input
        id="setting-{key}"
        type="number"
        {min}
        {max}
        {step}
        {value}
        {disabled}
        aria-invalid={err !== null}
        aria-describedby={err ? `settings-error-${key}` : undefined}
        onchange={setNumber(key, scale)}
      />
      {#if err}
        <p class="field-error" id="settings-error-{key}">{err}</p>
      {:else}
        <span class="hint">{hint}</span>
      {/if}
    </div>
  {/snippet}

  <div class="grid">
    {@render numberField('column_split', 'Gutter (% of the page width)', gutterPct, 20, 80, 0.5, 0.01, settings.single_column, 'Where the right column starts.')}
    {@render numberField('header_band', 'Header band (pt)', settings.header_band, 0, 200, 1, 1, false, 'Running heads above this line are dropped.')}
    {@render numberField('footer_band', 'Footer band (pt)', settings.footer_band, 0, 200, 1, 1, false, 'Page numbers below the bottom margin are dropped.')}
    {@render numberField('heading_min_size', 'Heading size (pt, at least)', settings.heading_min_size, 4, 72, 0.5, 1, false, 'Lines this big or bigger can be a section heading.')}
    {@render numberField('heading_wrap_gap', 'Heading wrap gap (pt)', settings.heading_wrap_gap ?? ENGINE_WRAP_GAP, 0, 200, 1, 1, false, 'Lines of one title sit within this distance; big titles need ~2× their size.')}
  </div>
</div>

<style>
  .panel {
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
  .columns {
    border: 0;
    padding: 0;
    margin: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 1rem;
  }
  .columns legend {
    font-size: 0.9rem;
    color: var(--muted);
    margin-bottom: 0.25rem;
  }
  .columns label {
    display: flex;
    align-items: center;
    gap: 0.4rem;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
    gap: 0.75rem 1rem;
  }
  .field > label {
    font-size: 0.9rem;
    color: var(--muted);
  }
  .hint {
    color: var(--muted);
    font-size: 0.8rem;
  }
</style>
