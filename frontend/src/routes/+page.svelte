<script lang="ts">
  import CampaignCard from "$lib/components/CampaignCard.svelte";
  import ThemeToggle from "$lib/components/ThemeToggle.svelte";
  // Status URL and poll cadence: window.STATUS_URL / VITE_* / defaults, all
  // documented in $lib/config.
  import { RELOAD_MS, resolveStatusUrl } from "$lib/config.js";
  import {
    isHttpUrl,
    isStale,
    shortDate,
    tickSummaryLabel,
  } from "$lib/derive.js";
  import { parseStatusDoc, type StatusDoc } from "$lib/status.js";

  // The last good document stays on screen through a failed poll; `error`
  // is a banner on top of it, never a replacement for it.
  let doc = $state<StatusDoc | null>(null);
  let problems = $state<string[]>([]);
  let error = $state<string | null>(null);

  // One request in flight at a time: a slow poll is abandoned when the next
  // one starts (or the page goes away), so responses never land out of order.
  let inflight: AbortController | null = null;

  async function load(): Promise<void> {
    inflight?.abort();
    const controller = new AbortController();
    inflight = controller;
    try {
      const res = await fetch(resolveStatusUrl(), {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const parsed = parseStatusDoc(await res.json());
      if (parsed.doc === null) {
        throw new Error(
          `not a status document (${parsed.problems.join("; ")})`,
        );
      }
      doc = parsed.doc;
      problems = parsed.problems;
      error = null;
    } catch (e) {
      if (controller.signal.aborted) return;
      error = e instanceof Error ? e.message : String(e);
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
    <div class="header-right">
      <div class="meta-block">
        {#if doc !== null && doc.campaigns_repo_url !== null}
          <p class="repo">
            campaigns repo:
            {#if isHttpUrl(doc.campaigns_repo_url)}
              <a href={doc.campaigns_repo_url} target="_blank" rel="noopener">
                {doc.campaigns_repo_url}
              </a>
            {:else}
              <code>{doc.campaigns_repo_url}</code>
            {/if}
          </p>
        {/if}
        {#if doc !== null}
          <p class="meta">
            generated <time datetime={doc.generated_at} title={doc.generated_at}
              >{shortDate(doc.generated_at) ?? doc.generated_at}</time
            >
          </p>
          {@const tick = tickSummaryLabel(doc.tick_summary)}
          {#if tick !== null}
            <!-- What the last reconcile cost; the operator's first signal
                 that a tick is growing (X1). -->
            <p class="meta tick">{tick}</p>
          {/if}
        {/if}
      </div>
      <ThemeToggle />
    </div>
  </header>
  {#if error !== null}
    <p class="banner error" role="alert">
      Cannot load status: {error}
      {#if doc !== null}
        — showing the last good document (generated {shortDate(
          doc.generated_at,
        ) ?? doc.generated_at}).
      {/if}
    </p>
  {/if}
  {#if doc === null}
    {#if error === null}<p>Loading…</p>{/if}
  {:else}
    {#if isStale(doc.generated_at, doc.tick_seconds)}
      <p class="banner stale">
        STALE — last reconcile <time
          datetime={doc.generated_at}
          title={doc.generated_at}
          >{shortDate(doc.generated_at) ?? doc.generated_at}</time
        >. The reconciler may be dead (this is not "no news").
      </p>
    {/if}
    {#each doc.warnings as w}<p class="warn">{w}</p>{/each}
    {#each problems as p}<p class="warn">status.json: {p}</p>{/each}
    {#each doc.campaigns as c (c.name)}
      <CampaignCard campaign={c} />
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
    align-items: baseline;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.5rem 1.5rem;
    margin-bottom: 1.5rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid var(--border);
  }

  .header-right {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    min-width: 0;
    max-width: 100%;
  }

  .meta-block {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 0.125rem;
    min-width: 0;
  }

  .repo,
  .meta {
    color: var(--muted-foreground);
    font-size: 0.8rem;
    margin: 0;
  }

  /* A long repo URL wraps inside the header instead of widening the page. */
  .repo {
    overflow-wrap: anywhere;
    text-align: right;
  }

  .repo a {
    color: var(--primary);
    text-decoration: none;
  }

  .repo a:hover {
    text-decoration: underline;
  }

  .repo code {
    color: inherit;
  }

  .tick {
    font-variant-numeric: tabular-nums;
    text-align: right;
  }

  .banner {
    padding: 0.5rem 1rem;
    border-radius: var(--radius);
    margin: 0 0 1rem;
  }

  .stale {
    background: var(--destructive);
    color: var(--on-strong);
  }

  .error {
    color: var(--destructive);
    border: 1px solid var(--destructive);
    background: var(--destructive-soft);
  }

  .warn {
    background: var(--muted);
    color: var(--foreground);
    border: 1px solid var(--warning);
    padding: 0.4rem 0.75rem;
    border-radius: var(--radius);
    margin: 0 0 0.75rem;
    font-size: 0.85rem;
  }
</style>
