<script lang="ts">
  import CampaignCard from "$lib/components/CampaignCard.svelte";
  import ThemeToggle from "$lib/components/ThemeToggle.svelte";
  // fetchJobs reads GET /api/v1/jobs (the read API); RELOAD_MS is the poll
  // cadence, both documented in $lib/config / $lib/api.
  import { fetchJobs, type JobSummary } from "$lib/api.js";
  import { RELOAD_MS } from "$lib/config.js";
  import { describeApiError } from "$lib/reasons.js";

  // The last good list stays on screen through a failed poll; `error` is a
  // banner on top of it, never a replacement for it.
  let jobs = $state<JobSummary[] | null>(null);
  let error = $state<string | null>(null);

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
      jobs = result;
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
    <ThemeToggle />
  </header>
  {#if error !== null}
    <p class="banner error" role="alert">{error}</p>
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
  h1 {
    font-size: 1.25rem;
    font-weight: 600;
    margin: 0;
  }

  .title-row {
    display: flex;
    align-items: center;
    gap: 0.6rem;
  }

  .logo {
    height: 1.6rem;
    width: auto;
  }

  .page {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.5rem 1.5rem;
    margin-bottom: 1.5rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid var(--border);
  }

  .banner {
    padding: 0.5rem 1rem;
    border-radius: var(--radius);
    margin: 0 0 1rem;
  }

  .error {
    color: var(--destructive);
    border: 1px solid var(--destructive);
    background: var(--destructive-soft);
  }

  .empty {
    color: var(--muted-foreground);
  }
</style>
