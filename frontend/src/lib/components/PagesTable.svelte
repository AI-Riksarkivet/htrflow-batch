<script lang="ts">
  // The full per-page table, rendered a slice at a time: a 480-row table is
  // nobody's first question, but it stays available for a hunt.
  import type { PageStat } from "$lib/run.js";

  let { pages, pageSize = 100 }: { pages: PageStat[]; pageSize?: number } =
    $props();

  let offset = $state(0);
  const last = $derived(Math.min(offset + pageSize, pages.length));
  const slice = $derived(pages.slice(offset, last));

  let downloadError = $state<string | null>(null);

  // <a download> is ignored cross-origin (the results bucket is a different
  // origin from this page), so the download goes through fetch + Blob: pull
  // the bytes ourselves, hand the browser a same-origin object URL to save.
  async function downloadAlto(url: string, page: string): Promise<void> {
    downloadError = null;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      try {
        const a = document.createElement("a");
        a.href = objectUrl;
        a.download = `${page}.xml`;
        a.click();
      } finally {
        URL.revokeObjectURL(objectUrl);
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      downloadError = `Could not download ${page}.xml: ${message}. Try "view" and save from there instead.`;
    }
  }
</script>

<div class="pager">
  <span class="range" aria-live="polite">
    {pages.length === 0
      ? "no pages"
      : `${offset + 1}–${last} of ${pages.length}`}
  </span>
  <button
    type="button"
    disabled={offset === 0}
    onclick={() => (offset = Math.max(0, offset - pageSize))}
  >
    previous
  </button>
  <button
    type="button"
    disabled={last >= pages.length}
    onclick={() => (offset = last)}
  >
    next
  </button>
</div>

{#if downloadError !== null}
  <p class="error" role="alert">{downloadError}</p>
{/if}

<div class="table-scroll">
  <table class="pages">
    <caption class="sr-only">Per-page results: id, status, seconds</caption>
    <thead>
      <tr>
        <th scope="col">page</th>
        <th scope="col">status</th>
        <th scope="col" class="num">seconds</th>
        <th scope="col">error</th>
        <th scope="col">alto</th>
      </tr>
    </thead>
    <tbody>
      {#each slice as r (r.id)}
        {@const alto = r.alto}
        <tr>
          <td class="pid">
            {#if r.source !== undefined}
              <a
                href={r.source}
                target="_blank"
                rel="noopener"
                title="source image">{r.id}</a
              >
            {:else}
              {r.id}
            {/if}
          </td>
          <td>
            <span class="chip {r.status}">{r.status}</span>
          </td>
          <td class="num">{r.seconds.toFixed(1)}</td>
          <td class="err">{r.error ?? ""}</td>
          <td class="alto">
            {#if alto !== undefined}
              <a href={`/alto?src=${encodeURIComponent(alto)}`}>view</a>
              <button type="button" onclick={() => downloadAlto(alto, r.id)}>
                download
              </button>
            {/if}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  .pager {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.8rem;
    color: var(--muted-foreground);
    margin: 0.5rem 0;
    font-variant-numeric: tabular-nums;
  }

  .pager button {
    font: inherit;
    color: var(--foreground);
    background: var(--muted);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 0.1rem 0.6rem;
    cursor: pointer;
  }

  .pager button:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .table-scroll {
    overflow-x: auto;
  }

  table.pages {
    width: 100%;
    max-width: 40rem;
    border-collapse: collapse;
    margin-bottom: 1.25rem;
    font-size: 12.5px;
    line-height: 1.35;
  }

  table.pages th {
    text-align: left;
    font-weight: 500;
    color: var(--muted-foreground);
    font-size: 10.5px;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    padding: 0.2rem 0.5rem;
    border-bottom: 1px solid var(--border);
  }

  table.pages th.num,
  table.pages td.num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }

  table.pages td {
    padding: 0.2rem 0.5rem;
    border-bottom: 1px solid var(--border);
  }

  table.pages tbody tr:last-child td {
    border-bottom: none;
  }

  td.pid {
    font-weight: 500;
  }

  td.pid a {
    color: var(--primary);
    text-decoration: none;
  }

  td.pid a:hover {
    text-decoration: underline;
  }

  td.err {
    color: var(--destructive);
    overflow-wrap: anywhere;
  }

  td.alto {
    white-space: nowrap;
  }

  td.alto a,
  td.alto button {
    font: inherit;
    color: var(--primary);
  }

  td.alto button {
    background: none;
    border: none;
    padding: 0;
    margin-left: 0.6ch;
    cursor: pointer;
    text-decoration: underline;
  }

  .error {
    font-size: 0.85rem;
  }

  .chip {
    width: fit-content;
  }

  .chip.ok {
    background: var(--success-soft);
    color: var(--success);
  }

  .chip.failed {
    background: var(--destructive-soft);
    color: var(--destructive);
  }
</style>
