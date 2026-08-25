<script lang="ts">
  import { browser } from "$app/environment";
  import {
    isStale,
    pagesLabel,
    progress,
    shortDate,
    viewerHref,
  } from "$lib/derive.js";
  import { statusDocSchema, type StatusDoc } from "$lib/status.js";

  const DEFAULT_STATUS_URL =
    "http://localhost:30900/htr-results/status/status.json";
  const RELOAD_MS = 60_000;
  const THEME_KEY = "htr-theme";

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

  // null = follow OS (`prefers-color-scheme`). Explicit choice persists to
  // localStorage; the page is prerendered, so localStorage is only touched in
  // the browser (never during the Node prerender pass).
  let theme = $state<"light" | "dark" | null>(
    browser ? (localStorage.getItem(THEME_KEY) as "light" | "dark" | null) : null,
  );

  function toggleTheme(): void {
    const effective =
      theme ??
      (browser && window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light");
    theme = effective === "dark" ? "light" : "dark";
    if (browser) localStorage.setItem(THEME_KEY, theme);
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

<main data-theme={theme}>
  <header class="page">
    <h1>HTR Campaigns</h1>
    <div class="header-right">
      <div class="meta-block">
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
        {#if doc !== null}<p class="meta">generated {doc.generated_at}</p>{/if}
      </div>
      <button
        class="theme-toggle"
        onclick={toggleTheme}
        title="Toggle light/dark theme"
        aria-label="Toggle light/dark theme"
      >
        ◐
      </button>
    </div>
  </header>
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
    {#each doc.warnings as w}<p class="warn">{w}</p>{/each}
    {#each doc.campaigns as c}
      <section class="campaign">
        <button class="camp" onclick={() => toggle(c.name)}>
          <span class="disclosure">{collapsed.has(c.name) ? "▸" : "▾"}</span>
          <span class="camp-name">{c.name}</span>
          {#if c.error !== null}
            <span class="chip needs-attention">broken</span>
          {:else}
            <span
              class="chip pipeline"
              title={c.pipeline_steps !== null && c.pipeline_steps.length > 0
                ? c.pipeline_steps.join(" → ")
                : undefined}>{c.pipeline}</span
            >
            <progress max="100" value={progress(c.totals)}></progress>
            <span class="counts">
              {c.totals.done}/{c.totals.total} volumes
              {#if pagesLabel(c.totals) !== null}
                <span class="pages">· {pagesLabel(c.totals)}</span>
              {/if}
            </span>
          {/if}
        </button>
        {#if c.error !== null}<p class="notice error-row">{c.error}</p>{/if}
        {#if c.orphans.length > 0}
          <p class="notice warn-row">
            orphaned results (in bucket, not in git): {c.orphans.join(", ")}
          </p>
        {/if}
        {#if !collapsed.has(c.name) && c.error === null}
          <table class="volumes">
            <thead>
              <tr>
                <th></th>
                <th>volume</th>
                <th>status</th>
                <th class="num">pages</th>
                <th class="num">attempts</th>
                <th>updated</th>
                <th>links</th>
              </tr>
            </thead>
            <tbody>
              {#each c.volumes as v}
                <tr class:planned={v.status === "pending"}>
                  <td class="thumb">
                    {#if v.thumbnail !== null}
                      <img src={v.thumbnail} alt="" loading="lazy" />
                    {/if}
                  </td>
                  <td class="vid">{v.id}</td>
                  <td>
                    <span class="status {v.status}">
                      <span class="dot"></span>
                      {v.status === "pending" ? "planned" : v.status}
                    </span>
                  </td>
                  <td class="num">
                    {v.pages_total !== null || v.pages_done !== null
                      ? `${v.pages_done ?? 0}/${v.pages_total ?? "?"}`
                      : "—"}
                  </td>
                  <td class="num">{v.attempts > 0 ? v.attempts : "—"}</td>
                  <td class="updated">{shortDate(v.updated) ?? "—"}</td>
                  <td class="links">
                    {#if v.status === "done"}
                      <a href={viewerHref(v)} target="_blank" rel="noopener"
                        >open</a
                      >
                    {/if}
                    <a
                      class="secondary"
                      href={v.source_manifest}
                      target="_blank"
                      rel="noopener">source</a
                    >
                    {#if v.failure_log !== null}
                      <a
                        class="danger"
                        href={v.failure_log}
                        target="_blank"
                        rel="noopener">log</a
                      >
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </section>
    {/each}
  {/if}
</main>

<style>
  main {
    --radius: 0.625rem;
    --background: oklch(0.985 0.004 80);
    --foreground: oklch(0.16 0.006 270);
    --card: oklch(0.993 0.002 80);
    --primary: oklch(0.37 0.19 250);
    --muted: oklch(0.955 0.006 260);
    --muted-foreground: oklch(0.45 0.012 260);
    --border: oklch(0.915 0.006 260);
    --success: oklch(0.65 0.2 145);
    --warning: oklch(0.75 0.18 75);
    --destructive: oklch(0.577 0.245 27.325);
    color-scheme: light;
  }

  /* Default: follow the OS when no explicit choice has been made. */
  @media (prefers-color-scheme: dark) {
    main {
      --background: oklch(0.13 0.006 270);
      --foreground: oklch(0.985 0 0);
      --card: oklch(0.17 0.008 270);
      --primary: oklch(0.68 0.16 250);
      --muted: oklch(0.22 0.008 270);
      --muted-foreground: oklch(0.65 0.01 260);
      --border: oklch(0.28 0.008 270);
      color-scheme: dark;
    }
  }

  /* Explicit choice (data-theme, from the toggle + localStorage) always
     wins over the OS default in both directions — attribute selectors
     out-specificity the plain `main` selector regardless of media state. */
  main[data-theme="dark"] {
    --background: oklch(0.13 0.006 270);
    --foreground: oklch(0.985 0 0);
    --card: oklch(0.17 0.008 270);
    --primary: oklch(0.68 0.16 250);
    --muted: oklch(0.22 0.008 270);
    --muted-foreground: oklch(0.65 0.01 260);
    --border: oklch(0.28 0.008 270);
    color-scheme: dark;
  }

  main[data-theme="light"] {
    --background: oklch(0.985 0.004 80);
    --foreground: oklch(0.16 0.006 270);
    --card: oklch(0.993 0.002 80);
    --primary: oklch(0.37 0.19 250);
    --muted: oklch(0.955 0.006 260);
    --muted-foreground: oklch(0.45 0.012 260);
    --border: oklch(0.915 0.006 260);
    color-scheme: light;
  }

  main {
    font-family:
      system-ui,
      -apple-system,
      "Segoe UI",
      sans-serif;
    max-width: 64rem;
    margin: 0 auto;
    padding: 1.5rem 1rem 3rem;
    background: var(--background);
    color: var(--foreground);
    line-height: 1.4;
  }

  h1 {
    font-size: 1.25rem;
    font-weight: 600;
    margin: 0;
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
  }

  .meta-block {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 0.125rem;
  }

  .theme-toggle {
    flex-shrink: 0;
    cursor: pointer;
    background: var(--muted);
    color: var(--foreground);
    border: 1px solid var(--border);
    border-radius: 999px;
    width: 1.8rem;
    height: 1.8rem;
    line-height: 1;
    font-size: 1rem;
    padding: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }

  .theme-toggle:hover {
    border-color: var(--primary);
    color: var(--primary);
  }

  .repo,
  .meta {
    color: var(--muted-foreground);
    font-size: 0.8rem;
    margin: 0;
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

  .stale {
    background: var(--destructive);
    color: var(--background);
    padding: 0.5rem 1rem;
    border-radius: var(--radius);
    margin: 0 0 1rem;
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

  .error {
    color: var(--destructive);
  }

  .campaign {
    background: var(--card);
    border: 1px solid var(--border);
    border-left: 3px solid var(--primary);
    border-radius: var(--radius);
    padding: 0.6rem 0.75rem;
    margin-bottom: 0.5rem;
  }

  .camp {
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 0.75rem;
    font-size: 1rem;
    font-weight: 600;
    background: none;
    border: none;
    padding: 0.3rem 0;
    width: 100%;
    text-align: left;
    color: var(--foreground);
    font-family: inherit;
  }

  .disclosure {
    color: var(--muted-foreground);
    font-size: 0.75rem;
    width: 1em;
  }

  .camp-name {
    flex-shrink: 0;
  }

  .camp progress {
    appearance: none;
    -webkit-appearance: none;
    accent-color: var(--primary);
    width: 8rem;
    height: 0.5rem;
    border: none;
    border-radius: 999px;
    background: var(--muted);
  }

  .camp progress::-webkit-progress-bar {
    background: var(--muted);
    border-radius: 999px;
  }

  .camp progress::-webkit-progress-value {
    background: var(--primary);
    border-radius: 999px;
  }

  .camp progress::-moz-progress-bar {
    background: var(--primary);
    border-radius: 999px;
  }

  .counts {
    color: var(--muted-foreground);
    font-size: 0.85rem;
    font-weight: 400;
    white-space: nowrap;
  }

  .pages {
    font-weight: 400;
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

  .chip.needs-attention {
    background: var(--destructive);
    color: var(--background);
  }

  .chip.pipeline {
    background: color-mix(in oklab, var(--primary) 15%, transparent);
    color: var(--primary);
  }

  .notice {
    font-size: 0.85rem;
    margin: 0.25rem 0 0;
  }

  .error-row {
    color: var(--destructive);
  }

  .warn-row {
    color: var(--warning);
  }

  table.volumes {
    width: 100%;
    border-collapse: collapse;
    margin-top: 0.5rem;
    font-size: 12.5px;
    line-height: 1.35;
  }

  table.volumes th {
    text-align: left;
    font-weight: 500;
    color: var(--muted-foreground);
    font-size: 10.5px;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    padding: 0.2rem 0.5rem;
    border-bottom: 1px solid var(--border);
  }

  table.volumes th.num,
  table.volumes td.num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }

  table.volumes td {
    padding: 0.2rem 0.5rem;
    border-bottom: 1px solid var(--border);
    vertical-align: middle;
  }

  table.volumes tbody tr:last-child td {
    border-bottom: none;
  }

  tr.planned {
    opacity: 0.65;
  }

  td.thumb {
    width: 1.6rem;
  }

  td.thumb img {
    width: 1.6rem;
    height: 1.6rem;
    object-fit: cover;
    border-radius: 3px;
    display: block;
  }

  td.vid {
    font-weight: 500;
    color: var(--foreground);
  }

  .status {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    white-space: nowrap;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
  }

  .status .dot {
    width: 0.5em;
    height: 0.5em;
    border-radius: 50%;
    background: currentColor;
    flex-shrink: 0;
  }

  .status.done {
    color: var(--success);
    background: color-mix(in oklab, var(--success) 15%, transparent);
  }

  .status.running {
    color: var(--primary);
    background: color-mix(in oklab, var(--primary) 15%, transparent);
  }

  .status.queued,
  .status.retry {
    color: var(--warning);
    background: color-mix(in oklab, var(--warning) 15%, transparent);
  }

  .status.needs-attention,
  .status.unreachable,
  .status.unsupported {
    color: var(--destructive);
    background: color-mix(in oklab, var(--destructive) 15%, transparent);
  }

  .status.pending {
    color: var(--muted-foreground);
    background: color-mix(in oklab, var(--muted-foreground) 15%, transparent);
  }

  td.updated {
    color: var(--muted-foreground);
    white-space: nowrap;
  }

  td.links {
    white-space: nowrap;
  }

  td.links a {
    color: var(--primary);
    text-decoration: none;
    margin-right: 0.75rem;
  }

  td.links a:last-child {
    margin-right: 0;
  }

  td.links a:hover {
    text-decoration: underline;
  }

  td.links a.secondary {
    color: var(--muted-foreground);
  }

  td.links a.danger {
    color: var(--destructive);
  }
</style>
