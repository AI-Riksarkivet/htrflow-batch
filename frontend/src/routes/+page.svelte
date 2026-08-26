<script lang="ts">
  import { browser } from "$app/environment";
  import CampaignCard from "$lib/components/CampaignCard.svelte";
  import { isStale, shortDate } from "$lib/derive.js";
  import { parseStatusDoc, type StatusDoc } from "$lib/status.js";

  const DEFAULT_STATUS_URL =
    "http://localhost:30900/htr-results/status/status.json";
  const RELOAD_MS = 60_000;
  const THEME_KEY = "htr-theme";

  // Resolved per fetch, not once at init: the deployment may inject
  // window.STATUS_URL late, and it lets the dev fixture be swapped in from the
  // browser console without a rebuild (see README).
  const statusUrl = (): string => window.STATUS_URL ?? DEFAULT_STATUS_URL;

  // The last good document stays on screen through a failed poll; `error`
  // is a banner on top of it, never a replacement for it.
  let doc = $state<StatusDoc | null>(null);
  let problems = $state<string[]>([]);
  let error = $state<string | null>(null);

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
      const parsed = parseStatusDoc(await res.json());
      if (parsed.doc === null) {
        throw new Error(`not a status document (${parsed.problems.join("; ")})`);
      }
      doc = parsed.doc;
      problems = parsed.problems;
      error = null;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
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
    <div class="title-row">
      <img class="logo" src="/ra.svg" alt="Riksarkivet" />
      <h1>HTR Campaigns</h1>
    </div>
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
        {#if doc !== null}
          <p class="meta">
            generated <time datetime={doc.generated_at} title={doc.generated_at}
              >{shortDate(doc.generated_at) ?? doc.generated_at}</time
            >
          </p>
        {/if}
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
    <p class="banner error" role="alert">
      Cannot load status: {error}
      {#if doc !== null}
        — showing the last good document (generated {shortDate(doc.generated_at) ??
          doc.generated_at}).
      {/if}
    </p>
  {/if}
  {#if doc === null}
    {#if error === null}<p>Loading…</p>{/if}
  {:else}
    {#if isStale(doc.generated_at, doc.tick_seconds)}
      <p class="banner stale">
        STALE — last reconcile {shortDate(doc.generated_at) ?? doc.generated_at}.
        The reconciler may be dead (this is not "no news").
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

  /* main is full-bleed so --background covers the whole viewport in dark
     mode (a centered max-width main leaves white body gutters); content is
     centered with padding instead of margin auto. */
  :global(body) {
    margin: 0;
  }

  main {
    font-family:
      system-ui,
      -apple-system,
      "Segoe UI",
      sans-serif;
    min-height: 100vh;
    box-sizing: border-box;
    padding: 1.5rem max(1rem, calc(50vw - 32rem)) 3rem;
    background: var(--background);
    color: var(--foreground);
    line-height: 1.4;
  }

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

  .banner {
    padding: 0.5rem 1rem;
    border-radius: var(--radius);
    margin: 0 0 1rem;
  }

  .stale {
    background: var(--destructive);
    color: var(--background);
  }

  .error {
    color: var(--destructive);
    border: 1px solid var(--destructive);
    background: color-mix(in oklab, var(--destructive) 8%, transparent);
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
