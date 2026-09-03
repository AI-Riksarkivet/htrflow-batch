<script lang="ts">
  // What a 480-page run looks like at a glance: counts and timing in a
  // strip, one cell per page, the failures spelled out, the full table on
  // request.
  import {
    formatDuration,
    pageStats,
    summarizeRun,
    type RunManifest,
  } from "$lib/run.js";
  import PageGrid from "./PageGrid.svelte";
  import PagesTable from "./PagesTable.svelte";

  let { manifest }: { manifest: RunManifest } = $props();

  const summary = $derived(
    summarizeRun(manifest.results, manifest.page_sources, manifest.viewer_url),
  );
  const pages = $derived(
    pageStats(manifest.results, manifest.page_sources, manifest.viewer_url),
  );

  function shortDigest(digest: string): string {
    return digest.slice(-12);
  }

  function secs(v: number | null): string {
    return v === null ? "—" : `${v.toFixed(1)} s`;
  }
</script>

<section class="summary" aria-label="run summary">
  <div class="field">
    <span class="label">volume</span>
    <span class="value">{manifest.volume}</span>
  </div>
  <div class="field">
    <span class="label">pipeline</span>
    <span class="value">{manifest.pipeline_id}</span>
  </div>
  <div class="field">
    <span class="label">htrflow</span>
    <span class="value">{manifest.htrflow_version}</span>
  </div>
  <div class="field">
    <span class="label">image</span>
    <span class="value mono">{shortDigest(manifest.image_digest)}</span>
  </div>
  <div class="field">
    <span class="label">pages</span>
    <span class="value num">
      {manifest.pages}
      <span class="sub">
        <span class="ok">{summary.ok} ok</span>
        · <span class:bad={summary.failed > 0}>{summary.failed} failed</span>
        · {summary.skipped} skipped
      </span>
    </span>
  </div>
  <div class="field">
    <span class="label">total time</span>
    <span class="value num">
      {formatDuration(summary.totalSeconds)}
      {#if manifest.wall_seconds !== undefined}
        <span class="sub">wall {formatDuration(manifest.wall_seconds)}</span>
      {/if}
    </span>
  </div>
  <div class="field">
    <span class="label">per page · median / p95 / max</span>
    <span class="value num">
      {secs(summary.median)} / {secs(summary.p95)} / {secs(summary.max)}
    </span>
  </div>
  {#if summary.slowest.length > 0}
    <div class="field wide">
      <span class="label">slowest pages</span>
      <span class="value num slowest">
        {#each summary.slowest as p (p.id)}
          <span class="slow"
            >{p.id} <span class="sub">{p.seconds.toFixed(1)} s</span></span
          >
        {/each}
      </span>
    </div>
  {/if}
</section>

{#if pages.length > 0}
  <PageGrid {pages} max={summary.max} />
{/if}

{#if summary.failedPages.length > 0}
  <section class="failed" aria-label="failed pages">
    <h2>
      {summary.failedPages.length} failed page{summary.failedPages.length === 1
        ? ""
        : "s"}
    </h2>
    <ul>
      {#each summary.failedPages as p (p.id)}
        <li>
          <span class="pid">
            {#if p.source !== undefined}
              <a
                href={p.source}
                target="_blank"
                rel="noopener"
                title="source image">{p.id}</a
              >
            {:else}
              {p.id}
            {/if}
          </span>
          <span class="err">{p.error ?? "no error recorded"}</span>
        </li>
      {/each}
    </ul>
  </section>
{/if}

{#if pages.length > 0}
  <details class="all-pages">
    <summary>all {pages.length} pages</summary>
    <PagesTable {pages} />
  </details>
{/if}

<style>
  .summary {
    display: flex;
    flex-wrap: wrap;
    gap: 0.9rem 1.5rem;
    background: var(--card);
    border: 1px solid var(--border);
    border-left: 3px solid var(--primary);
    border-radius: var(--radius);
    padding: 0.75rem 1rem;
    margin-bottom: 1rem;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 0.1rem;
    min-width: 0;
  }

  .field.wide {
    flex-basis: 100%;
  }

  .label {
    font-size: 10.5px;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    color: var(--muted-foreground);
  }

  .value {
    font-size: 0.9rem;
    overflow-wrap: anywhere;
  }

  .value.num {
    font-variant-numeric: tabular-nums;
  }

  .value.mono {
    font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
  }

  .sub {
    color: var(--muted-foreground);
    font-size: 0.8rem;
  }

  .ok {
    color: var(--success);
  }

  .bad {
    color: var(--destructive);
    font-weight: 600;
  }

  .slowest {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem 1rem;
  }

  .failed {
    margin-bottom: 1rem;
  }

  .failed h2 {
    font-size: 0.9rem;
    font-weight: 600;
    color: var(--destructive);
    margin: 0 0 0.35rem;
  }

  .failed ul {
    list-style: none;
    margin: 0;
    padding: 0;
    font-size: 12.5px;
  }

  .failed li {
    display: grid;
    grid-template-columns: 4rem minmax(0, 1fr);
    gap: 0.5rem;
    padding: 0.2rem 0;
    border-bottom: 1px solid var(--border);
  }

  .failed .pid {
    font-weight: 500;
  }

  .failed .pid a {
    color: var(--primary);
    text-decoration: none;
  }

  .failed .pid a:hover {
    text-decoration: underline;
  }

  .failed .err {
    color: var(--destructive);
    overflow-wrap: anywhere;
    font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
  }

  details.all-pages {
    margin-bottom: 1.25rem;
  }

  details.all-pages summary {
    cursor: pointer;
    font-size: 0.85rem;
    color: var(--muted-foreground);
  }
</style>
