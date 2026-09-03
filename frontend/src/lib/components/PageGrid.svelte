<script lang="ts">
  // One small cell per page: colour = bucket, bar height = scale — both
  // computed once by pageRows(). Cells are buttons with a roving tabindex
  // so the grid is one tab stop and arrow keys walk the pages; hover/focus
  // prints the page name and seconds in the readout line (and the native
  // title).
  import type { PageRow } from "$lib/run.js";

  let { rows }: { rows: PageRow[] } = $props();

  // Index = PageRow["bucket"]; index 3 (an outcome the wrapper never
  // emits) and the reserved 4 fall back to the base .cell style.
  const BUCKET_CLASS = ["ok", "failed", "skipped"] as const;

  let readout = $state<string | null>(null);
  let focusIdx = $state(0);
  let grid: HTMLDivElement | undefined = $state();

  function label(r: PageRow): string {
    return `page ${r.name} · ${r.seconds.toFixed(1)} s · ${r.status}`;
  }

  function moveFocus(next: number): void {
    const clamped = Math.max(0, Math.min(rows.length - 1, next));
    focusIdx = clamped;
    const cell = grid?.children[clamped];
    if (cell instanceof HTMLElement) cell.focus();
  }

  function onKey(event: KeyboardEvent, i: number): void {
    const step: Record<string, number> = {
      ArrowRight: i + 1,
      ArrowLeft: i - 1,
      Home: 0,
      End: rows.length - 1,
    };
    const next = step[event.key];
    if (next === undefined) return;
    event.preventDefault();
    moveFocus(next);
  }
</script>

<div class="page-grid">
  <p class="readout" aria-hidden="true">
    {readout ?? "hover or focus a cell: page id and seconds"}
  </p>
  <div
    class="grid"
    role="group"
    aria-label="pages: colour is status, height is seconds"
    bind:this={grid}
  >
    {#each rows as r, i (r.name)}
      <button
        type="button"
        class="cell {BUCKET_CLASS[r.bucket] ?? ''}"
        style="--t: {r.scale}"
        tabindex={i === focusIdx ? 0 : -1}
        aria-label={label(r)}
        title={label(r)}
        onmouseenter={() => (readout = label(r))}
        onmouseleave={() => (readout = null)}
        onfocus={() => {
          focusIdx = i;
          readout = label(r);
        }}
        onblur={() => (readout = null)}
        onkeydown={(e) => onKey(e, i)}
      >
        <span class="bar"></span>
      </button>
    {/each}
  </div>
</div>

<style>
  .page-grid {
    margin-bottom: 1rem;
  }

  .readout {
    margin: 0 0 0.35rem;
    font-size: 0.8rem;
    color: var(--muted-foreground);
    font-variant-numeric: tabular-nums;
    min-height: 1.2em;
  }

  .grid {
    display: flex;
    flex-wrap: wrap;
    gap: 2px;
  }

  .cell {
    display: flex;
    align-items: flex-end;
    width: 10px;
    height: 16px;
    padding: 0;
    border: 1px solid transparent;
    border-radius: 2px;
    background: var(--muted);
    cursor: default;
    --c: var(--muted-foreground);
  }

  .cell.ok {
    --c: var(--success);
  }

  .cell.failed {
    --c: var(--destructive);
    background: var(--destructive-soft);
    border-color: var(--destructive);
  }

  .cell.skipped {
    --c: var(--muted-foreground);
    background: transparent;
    border-color: var(--border);
  }

  .bar {
    display: block;
    width: 100%;
    height: calc(var(--t) * 100%);
    background: var(--c);
    border-radius: 1px;
  }

  .cell:hover {
    border-color: var(--foreground);
  }

  .cell:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 1px;
  }
</style>
