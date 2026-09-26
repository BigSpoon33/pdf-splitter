<script lang="ts">
  import type { CreatedJob } from '../lib/api'
  import type { JobMode } from '../lib/route'
  import DropZone from './DropZone.svelte'

  interface Props {
    /** The upload landed: `mode` is the entry point it came through (`undefined` = chapters, the default flow). */
    oncreated: (id: string, mode?: JobMode) => void
    upload?: (file: File, onProgress: (fraction: number) => void) => Promise<CreatedJob>
  }

  let { oncreated, upload }: Props = $props()
</script>

<div class="entries">
  <section class="entry" aria-labelledby="entry-chapters">
    <h2 id="entry-chapters">Split by chapters</h2>
    <p>
      The book's outline or its headings become the section list; check it, adjust it, then get one PDF per chapter.
    </p>
    <DropZone oncreated={(id) => oncreated(id)} {upload} />
  </section>
  <section class="entry" aria-labelledby="entry-ranges">
    <h2 id="entry-ranges">Split by page ranges</h2>
    <p>Type the pages you want — <code>1-10, 15-20, 40</code> — and get one PDF per range, exactly those pages.</p>
    <DropZone oncreated={(id) => oncreated(id, 'ranges')} {upload} />
  </section>
</div>

<style>
  .entries {
    display: grid;
    gap: 1.5rem;
  }
  @media (min-width: 40rem) {
    .entries {
      grid-template-columns: 1fr 1fr;
      gap: 1.25rem;
    }
  }
  h2 {
    font-size: 1.2rem;
    margin: 0 0 0.35rem;
  }
  p {
    margin: 0 0 0.75rem;
    color: var(--muted);
  }
  code {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    color: var(--fg);
  }
</style>
