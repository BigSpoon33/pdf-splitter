<script lang="ts">
  import Home from './components/Home.svelte'
  import JobPage from './components/JobPage.svelte'
  import Privacy from './components/Privacy.svelte'
  import Terms from './components/Terms.svelte'
  import { href, jobPath, linkClick, navigate, onNavigate, parseRoute } from './lib/route'

  let path = $state(href())
  const route = $derived(parseRoute(path))

  $effect(() => onNavigate((p) => (path = p)))
</script>

<header class="site">
  <a href="/" onclick={(e) => linkClick(e, '/')} class="brand">PDF Splitter</a>
  <p class="tagline">One big PDF in, one PDF per chapter — or per page range — out.</p>
</header>

<main>
  {#if route.name === 'home'}
    <Home oncreated={(id, mode) => navigate(jobPath(id, mode))} />
  {:else if route.name === 'job'}
    {#key route.id}
      <JobPage id={route.id} mode={route.mode} />
    {/key}
  {:else if route.name === 'privacy'}
    <Privacy />
  {:else if route.name === 'terms'}
    <Terms />
  {:else}
    <section class="card" role="alert">
      <h1>Page not found</h1>
      <p><a href="/" onclick={(e) => linkClick(e, '/')}>Go to the upload page</a></p>
    </section>
  {/if}
</main>

<footer class="site-footer">
  <a href="/privacy" onclick={(e) => linkClick(e, '/privacy')}>Privacy</a>
  <a href="/terms" onclick={(e) => linkClick(e, '/terms')}>Terms</a>
</footer>

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
  h1 {
    font-size: 1.25rem;
    margin: 0 0 0.5rem;
  }
  .site-footer {
    display: flex;
    gap: 1.25rem;
    margin-top: 2.5rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border);
    font-size: 0.9rem;
  }
  .site-footer a {
    color: var(--muted);
  }
</style>
