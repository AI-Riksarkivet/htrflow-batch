<script lang="ts">
  import CampaignCard from "$lib/components/CampaignCard.svelte";
  import ThemeToggle from "$lib/components/ThemeToggle.svelte";
  // fetchJobs reads GET /api/v1/jobs (the read API); RELOAD_MS is the poll
  // cadence, both documented in $lib/config / $lib/api.
  import { fetchJobs, fetchVersion, type JobSummary } from "$lib/api.js";
  import { RELOAD_MS, REPO_URL } from "$lib/config.js";
  import { describeApiError, describeUnreadable } from "$lib/reasons.js";

  // The last good list stays on screen through a failed poll; `error` is a
  // banner on top of it, never a replacement for it.
  let jobs = $state<JobSummary[] | null>(null);
  let unreadable = $state(0);
  let error = $state<string | null>(null);

  // What is deployed, read once — nothing can change it while the page is
  // open, and a version nobody could fetch is simply not shown: it is a
  // footnote in the header, never a reason for an alert over the list.
  let version = $state<string | null>(null);
  let webVersion = $state("");

  async function loadVersion(): Promise<void> {
    try {
      const answer = await fetchVersion();
      version = `htrflow-batch ${answer.version}`;
      webVersion = answer.web;
    } catch {
      // Nothing to name the build with; the header just has no version.
    }
  }

  // One request in flight at a time: a slow poll is abandoned when the next
  // one starts (or the page goes away), so responses never land out of order.
  let inflight: AbortController | null = null;

  async function load(): Promise<void> {
    inflight?.abort();
    const controller = new AbortController();
    inflight = controller;
    try {
      const result = await fetchJobs();
      if (controller.signal.aborted) return;
      jobs = result.jobs;
      unreadable = result.unreadable;
      error = null;
    } catch (e) {
      if (controller.signal.aborted) return;
      // One sentence for the reader: what is wrong, that the list on
      // screen is the older one, and that it retries on its own. The
      // transport detail (a fetch error string, a ZodError) never reaches
      // the banner — see $lib/reasons.
      error = describeApiError(e, jobs !== null);
    }
  }

  $effect(() => {
    void loadVersion();
    void load();
    const timer = setInterval(() => void load(), RELOAD_MS);
    return () => {
      clearInterval(timer);
      inflight?.abort();
    };
  });
</script>

<main>
  <header class="page">
    <div class="title-row">
      <img class="logo" src="/ra.svg" alt="Riksarkivet" />
      <h1>HTR Campaigns</h1>
    </div>
    <div class="header-right">
      {#if version !== null}
        <span class="version" title="read API {webVersion}">{version}</span>
      {/if}
      <!-- The GitHub mark, inline: the page loads nothing from a third
           origin (its CSP would not allow it anyway). -->
      <a
        class="repo"
        href={REPO_URL}
        target="_blank"
        rel="noopener"
        aria-label="htrflow-batch on GitHub"
      >
        <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"
          ><path
            fill="currentColor"
            d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.6 7.6 0 0 1 2-.27c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"
          /></svg
        >
      </a>
      <ThemeToggle />
    </div>
  </header>
  {#if error !== null}
    <p class="banner error" role="alert">{error}</p>
  {:else if unreadable > 0}
    <p class="banner error" role="alert">{describeUnreadable(unreadable)}</p>
  {/if}
  {#if jobs === null}
    {#if error === null}<p>Loading…</p>{/if}
  {:else if jobs.length === 0}
    <p class="empty">No campaigns.</p>
  {:else}
    {#each jobs as job (job.namespace + "/" + job.name)}
      <CampaignCard {job} />
    {/each}
  {/if}
</main>

<style>
  .title-row {
    display: flex;
    align-items: center;
    gap: 0.6rem;
  }

  .logo {
    height: 1.6rem;
    width: auto;
  }

  /* The only route whose header centres vertically (app.css: .page) — a
     single-line title needs no top alignment. */
  .page {
    align-items: center;
  }

  /* Both sit in the muted colour and take the link colour on hover: the
     header is chrome, not content, in either theme. */
  .version {
    color: var(--muted-foreground);
    font-size: 0.8rem;
    font-variant-numeric: tabular-nums;
  }

  .repo {
    display: inline-flex;
    color: var(--muted-foreground);
  }

  .repo:hover {
    color: var(--primary);
  }

  .repo:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
    border-radius: 3px;
  }

  .banner {
    padding: 0.5rem 1rem;
    border-radius: var(--radius);
    margin: 0 0 1rem;
  }

  .error {
    border: 1px solid var(--destructive);
    background: var(--destructive-soft);
  }

  .empty {
    color: var(--muted-foreground);
  }
</style>
