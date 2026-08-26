<script lang="ts">
  // The full per-page table, rendered a slice at a time: a 480-row table is
  // nobody's first question, but it stays available for a hunt.
  import type { PageStat } from "$lib/run.js";

  let { pages, pageSize = 100 }: { pages: PageStat[]; pageSize?: number } =
    $props();

  let offset = $state(0);
  const last = $derived(Math.min(offset + pageSize, pages.length));
  const slice = $derived(pages.slice(offset, last));
</script>

<div class="pager">
  <span class="range" aria-live="polite">
    {pages.length === 0 ? "no pages" : `${offset + 1}–${last} of ${pages.length}`}
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

<div class="table-scroll">
  <table class="pages">
    <caption class="sr-only">Per-page results: id, status, seconds</caption>
    <thead>
      <tr>
        <th scope="col">page</th>
        <th scope="col">status</th>
        <th scope="col" class="num">seconds</th>
        <th scope="col">error</th>
      </tr>
    </thead>
    <tbody>
      {#each slice as r (r.id)}
        <tr>
          <td class="pid">{r.id}</td>
          <td>
            <span class="chip {r.status}">{r.status}</span>
          </td>
          <td class="num">{r.seconds.toFixed(1)}</td>
          <td class="err">{r.error ?? ""}</td>
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

  td.err {
    color: var(--destructive);
    overflow-wrap: anywhere;
  }

  .chip {
    font-size: 0.7rem;
    font-weight: 500;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    background: var(--muted);
    color: var(--muted-foreground);
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
