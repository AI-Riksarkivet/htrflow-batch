<script lang="ts">
  import { isStale, pagesLabel, progress, viewerHref } from "$lib/derive.js";
  import { statusDocSchema, type StatusDoc } from "$lib/status.js";

  const DEFAULT_STATUS_URL =
    "http://localhost:30900/htr-results/status/status.json";
  const RELOAD_MS = 60_000;

  // Resolved per fetch, not once at init: the deployment may inject
  // window.STATUS_URL late, and it lets the dev fixture be swapped in from the
  // browser console without a rebuild (see README).
  const statusUrl = (): string => window.STATUS_URL ?? DEFAULT_STATUS_URL;

  let doc = $state<StatusDoc | null>(null);
  let error = $state<string | null>(null);
  let collapsed = $state<Set<string>>(new Set()); // expanded by default
  function toggle(name: string): void {
    const next = new Set(collapsed);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    collapsed = next;
  }

  async function load(): Promise<void> {
    try {
      const res = await fetch(statusUrl(), { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      doc = statusDocSchema.parse(await res.json());
      error = null;
    } catch (e) {
      error = String(e);
    }
  }

  $effect(() => {
    void load();
    const timer = setInterval(() => void load(), RELOAD_MS);
    return () => clearInterval(timer);
  });
</script>

<main>
  <h1>HTR Campaigns</h1>
  {#if doc !== null && doc.campaigns_repo_url !== null}
    <p class="repo">
      campaigns repo:
      {#if doc.campaigns_repo_url.startsWith("http")}
        <a href={doc.campaigns_repo_url} target="_blank" rel="noopener">
          {doc.campaigns_repo_url}
        </a>
      {:else}
        <code>{doc.campaigns_repo_url}</code>
      {/if}
    </p>
  {/if}
  {#if error !== null}
    <p class="error">Cannot load status: {error}</p>
  {:else if doc === null}
    <p>Loading…</p>
  {:else}
    {#if isStale(doc.generated_at, doc.tick_seconds)}
      <p class="stale">
        STALE — last reconcile {doc.generated_at}. The reconciler may be dead
        (this is not "no news").
      </p>
    {/if}
    <p class="meta">generated {doc.generated_at}</p>
    {#each doc.warnings as w}<p class="warn">{w}</p>{/each}
    {#each doc.campaigns as c}
      <section>
        <button class="camp" onclick={() => toggle(c.name)}>
          {c.name}
          {#if c.error !== null}<span class="chip needs-attention">broken</span>
          {:else}
            <span class="chip">{c.pipeline}</span>
            <progress max="100" value={progress(c.totals)}></progress>
            {c.totals.done}/{c.totals.total} volumes
            {#if pagesLabel(c.totals) !== null}
              <span class="pages">· {pagesLabel(c.totals)}</span>
            {/if}
          {/if}
        </button>
        {#if c.pipeline_steps !== null && c.pipeline_steps.length > 0}
          <p class="steps">{c.pipeline_steps.join(" → ")}</p>
        {/if}
        {#if c.error !== null}<p class="error">{c.error}</p>{/if}
        {#if c.orphans.length > 0}
          <p class="warn">
            orphaned results (in bucket, not in git): {c.orphans.join(", ")}
          </p>
        {/if}
        {#if !collapsed.has(c.name) && c.error === null}
          <div class="grid">
            {#each c.volumes as v}
              <a
                class="card"
                class:planned={v.status === "pending"}
                href={viewerHref(v)}
                target="_blank"
                rel="noopener"
              >
                {#if v.thumbnail !== null}
                  <img src={v.thumbnail} alt="" loading="lazy" />
                {/if}
                <span class="chip {v.status}">
                  {v.status === "pending" ? "planned" : v.status}
                </span>
                <strong>{v.id}</strong>
                {#if v.pages_total !== null || v.pages_done !== null}
                  <small>{pagesLabel(v) ?? `${v.pages_done} pages`}</small>
                {/if}
                {#if v.attempts > 0}<small>attempts: {v.attempts}</small>{/if}
              </a>
            {/each}
          </div>
        {/if}
      </section>
    {/each}
  {/if}
</main>

<style>
  main {
    font-family: system-ui, sans-serif;
    max-width: 60rem;
    margin: 0 auto;
    padding: 1rem;
  }
  .stale {
    background: #b91c1c;
    color: #fff;
    padding: 0.5rem 1rem;
    border-radius: 4px;
  }
  .warn {
    background: #fef3c7;
    padding: 0.25rem 0.75rem;
    border-radius: 4px;
  }
  .error {
    color: #b91c1c;
  }
  .meta {
    color: #6b7280;
    font-size: 0.85rem;
  }
  .camp {
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 0.75rem;
    font-size: 1.25rem;
    font-weight: 600;
    background: none;
    border: none;
    padding: 0.5rem 0;
    width: 100%;
    text-align: left;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
    gap: 0.75rem;
  }
  .card {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    padding: 0.75rem;
    text-decoration: none;
    color: inherit;
  }
  .card:hover {
    border-color: #2563eb;
  }
  .chip {
    font-size: 0.7rem;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    background: #e5e7eb;
    width: fit-content;
  }
  .chip.done {
    background: #bbf7d0;
  }
  .chip.running {
    background: #bfdbfe;
  }
  .chip.queued,
  .chip.retry {
    background: #fef08a;
  }
  .chip.needs-attention,
  .chip.unreachable,
  .chip.unsupported {
    background: #fecaca;
  }
  .card img {
    width: 100%;
    aspect-ratio: 3/4;
    object-fit: cover;
    border-radius: 4px;
  }
  .repo {
    color: #6b7280;
    font-size: 0.85rem;
  }
  .steps {
    color: #6b7280;
    font-size: 0.8rem;
    margin: 0 0 0.5rem;
  }
  .pages {
    font-weight: 400;
    color: #6b7280;
  }
  .card.planned {
    border-style: dashed;
  }
</style>
