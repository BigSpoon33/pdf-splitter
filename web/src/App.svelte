<script lang="ts">
  import DropZone from './components/DropZone.svelte'
  import JobStatus from './components/JobStatus.svelte'
  import { jobPath, navigate, onNavigate, parseRoute } from './lib/route'

  let pathname = $state(location.pathname)
  const route = $derived(parseRoute(pathname))

  $effect(() => onNavigate((p) => (pathname = p)))

  function home(e: MouseEvent) {
    // Let modified clicks open a new tab as usual.
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    navigate('/')
  }
</script>

<header class="site">
  <a href="/" onclick={home} class="brand">PDF Splitter</a>
  <p class="tagline">One big PDF in, one PDF per chapter out.</p>
</header>

<main>
  {#if route.name === 'home'}
    <DropZone oncreated={(id) => navigate(jobPath(id))} />
  {:else if route.name === 'job'}
    {#key route.id}
      <JobStatus id={route.id} />
    {/key}
    <p class="again"><a href="/" onclick={home}>Split another PDF</a></p>
  {:else}
    <section class="card" role="alert">
      <h1>Page not found</h1>
      <p><a href="/" onclick={home}>Go to the upload page</a></p>
    </section>
  {/if}
</main>

<style>
  .site {
    margin-bottom: 1.5rem;
  }
  .brand {
    font-size: 1.5rem;
    font-weight: 700;
    color: var(--fg);
    text-decoration: none;
  }
  .tagline {
    margin: 0.25rem 0 0;
    color: var(--muted);
  }
  .again {
    margin-top: 1rem;
  }
  h1 {
    font-size: 1.25rem;
    margin: 0 0 0.5rem;
  }
</style>
