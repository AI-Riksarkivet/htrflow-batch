<script lang="ts">
  import { browser } from "$app/environment";
  import {
    isTerminalLog,
    parseRunLog,
    splitLogLine,
    type LogGroup,
  } from "$lib/runlog.js";

  const THEME_KEY = "htr-theme";
  // Matches the wrapper's LOG_SHIP_SECONDS default: polling faster than the
  // pod uploads buys nothing.
  const LIVE_MS = 15_000;

  interface PageResult {
    status: string;
    seconds: number;
    error?: string;
  }

  interface RunManifest {
    volume: string;
    pipeline_id: string;
    htrflow_version: string;
    image_digest: string;
    pages: number;
    results: Record<string, PageResult>;
    pipeline_yaml?: string;
  }

  function isRunManifest(v: unknown): v is RunManifest {
    if (v === null || typeof v !== "object") return false;
    const m = v as Record<string, unknown>;
    return (
      typeof m.volume === "string" &&
      typeof m.pipeline_id === "string" &&
      typeof m.htrflow_version === "string" &&
      typeof m.image_digest === "string" &&
      typeof m.pages === "number" &&
      typeof m.results === "object" &&
      m.results !== null &&
      (m.pipeline_yaml === undefined || typeof m.pipeline_yaml === "string")
    );
  }

  // The route is opened as /log?log=<url>&manifest=<url> on a prerendered,
  // client-only SPA (see +layout.ts) — there is no SvelteKit load function
  // here, just window.location once we're in the browser.
  function queryParam(name: string): string | null {
    if (!browser) return null;
    return new URLSearchParams(window.location.search).get(name);
  }

  const logUrl = queryParam("log");
  const manifestUrl = queryParam("manifest");
  // live=1: the campaign table links a volume that is still in flight. The
  // wrapper re-uploads the log while it runs, so we re-fetch on its cadence
  // and stop once the wrapper's terminal line shows up.
  const startedLive = queryParam("live") === "1";

  let logText = $state<string | null>(null);
  let logError = $state<string | null>(null);
  let manifest = $state<RunManifest | null>(null);
  let live = $state(startedLive);
  let updatedAt = $state<string | null>(null);
  // Follow the tail only while the reader is already at the bottom; a reader
  // who scrolled up to look at something must not be yanked back down.
  let stickToBottom = $state(true);

  async function loadLog(): Promise<void> {
    if (logUrl === null) {
      logError = "no log URL given";
      return;
    }
    try {
      // no-cache (not no-store): the browser revalidates with the object's
      // ETag and gets a 304 when nothing changed, instead of re-pulling a
      // multi-MB log every poll.
      const res = await fetch(logUrl, { cache: "no-cache" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const text = await res.text();
      logError = null;
      if (text !== logText) {
        logText = text;
        updatedAt = new Date().toLocaleTimeString();
      }
      if (live && isTerminalLog(text)) live = false;
    } catch (e) {
      // A live volume's log may not exist yet (first upload pending) — keep
      // polling rather than freezing on the first 404.
      if (!live) logError = String(e);
    }
  }

  function onScroll(): void {
    stickToBottom =
      window.innerHeight + window.scrollY >= document.body.scrollHeight - 40;
  }

  async function loadManifest(): Promise<void> {
    if (manifestUrl === null) return;
    try {
      const res = await fetch(manifestUrl, { cache: "no-cache" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: unknown = await res.json();
      if (isRunManifest(data)) manifest = data;
    } catch {
      // Missing/failed manifest fetch → skip the summary card gracefully;
      // the raw log still renders on its own.
    }
  }

  $effect(() => {
    void loadLog();
    void loadManifest();
  });

  $effect(() => {
    if (!live) return;
    const timer = setInterval(() => {
      void loadLog();
      if (manifest === null) void loadManifest();
    }, LIVE_MS);
    return () => clearInterval(timer);
  });

  $effect(() => {
    // Re-runs on every log update; scrolls only while following.
    void logText;
    if (live && stickToBottom && browser) {
      requestAnimationFrame(() => window.scrollTo(0, document.body.scrollHeight));
    }
  });

  const parsed = $derived<{ groups: LogGroup[] } | null>(
    logText !== null ? parseRunLog(logText) : null,
  );

  const sortedPages = $derived(
    manifest !== null
      ? Object.entries(manifest.results).sort(([a], [b]) => a.localeCompare(b))
      : [],
  );

  const totalSeconds = $derived(
    manifest !== null
      ? Object.values(manifest.results)
          .reduce((acc, r) => acc + r.seconds, 0)
          .toFixed(1)
      : null,
  );

  function shortDigest(digest: string): string {
    return digest.slice(-12);
  }

  // Tint for the per-line level chip. WARNING/ERROR/CRITICAL get the same
  // treatment as the existing group-level tinting; INFO/DEBUG stay neutral.
  function levelClass(level: string): string {
    if (level === "WARNING") return "warning";
    if (level === "ERROR" || level === "CRITICAL") return "destructive";
    return "muted";
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
</script>

<svelte:window onscroll={onScroll} />

<main data-theme={theme}>
  <header class="page">
    <div class="title-block">
      <a class="back" href="/">← campaigns</a>
      <div class="title-row">
        <img class="logo" src="/ra.svg" alt="Riksarkivet" />
        <h1>Run log{manifest !== null ? ` · ${manifest.volume}` : ""}</h1>
      </div>
    </div>
    <div class="header-right">
      {#if live}
        <span class="live-badge"
          ><span class="pulse"></span>live{updatedAt !== null
            ? ` · updated ${updatedAt}`
            : " · waiting for first upload"}</span
        >
      {:else if startedLive && updatedAt !== null}
        <span class="live-badge finished">finished · {updatedAt}</span>
      {/if}
      {#if logUrl !== null}
        <a class="raw" href={logUrl} target="_blank" rel="noopener">raw</a>
      {/if}
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

  {#if manifest !== null}
    <section class="summary">
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
        <span class="label">pages</span>
        <span class="value num">{manifest.pages}</span>
      </div>
      <div class="field">
        <span class="label">total time</span>
        <span class="value num">{totalSeconds}s</span>
      </div>
      <div class="field">
        <span class="label">image</span>
        <span class="value mono">{shortDigest(manifest.image_digest)}</span>
      </div>
    </section>

    {#if sortedPages.length > 0}
      <table class="pages">
        <thead>
          <tr>
            <th>page</th>
            <th>status</th>
            <th class="num">seconds</th>
          </tr>
        </thead>
        <tbody>
          {#each sortedPages as [id, r] (id)}
            <tr>
              <td class="pid">{id}</td>
              <td>
                <span class="chip {r.status === 'ok' ? 'success' : 'destructive'}">
                  {r.status}
                </span>
              </td>
              <td class="num">{r.seconds.toFixed(1)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}

    {#if manifest.pipeline_yaml}
      <details class="pipeline-yaml">
        <summary>pipeline</summary>
        <pre>{manifest.pipeline_yaml}</pre>
      </details>
    {/if}
  {/if}

  <section class="log">
    {#if logError !== null}
      <p class="error">Cannot load log: {logError}</p>
    {:else if parsed === null}
      <p>Loading…</p>
    {:else if parsed.groups.length === 0}
      <p class="muted">Log is empty.</p>
    {:else}
      {#snippet logLine(line: string)}
        {@const s = splitLogLine(line)}
        {#if s.time === null}
          <div class="log-line full">{s.msg}</div>
        {:else}
          <div class="log-line">
            <span class="log-time" title={line.slice(0, 23)}>{s.time}</span>
            <span class="log-level {levelClass(s.level ?? '')}">{s.level}</span>
            <span class="log-msg">{s.msg}</span>
          </div>
        {/if}
      {/snippet}

      {#each parsed.groups as g, i (i)}
        {#if g.kind === "http"}
          <details class="group http">
            <summary
              >{g.lines.length} HTTP request{g.lines.length === 1 ? "" : "s"}</summary
            >
            <div class="lines">
              {#each g.lines as line}
                {@render logLine(line)}
              {/each}
            </div>
          </details>
        {:else}
          <div class="group {g.kind}">
            {#each g.lines as line}
              {@render logLine(line)}
            {/each}
          </div>
        {/if}
      {/each}
    {/if}
  </section>
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

  .page {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.5rem 1.5rem;
    margin-bottom: 1.5rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid var(--border);
  }

  .title-block {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
  }

  .back {
    color: var(--muted-foreground);
    font-size: 0.8rem;
    text-decoration: none;
    width: fit-content;
  }

  .back:hover {
    color: var(--primary);
    text-decoration: underline;
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

  .header-right {
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }

  .raw {
    color: var(--muted-foreground);
    font-size: 0.85rem;
    text-decoration: none;
  }

  .live-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.75rem;
    font-weight: 500;
    padding: 0.15rem 0.6rem;
    border-radius: 999px;
    color: var(--primary);
    background: color-mix(in oklab, var(--primary) 15%, transparent);
    white-space: nowrap;
  }

  .live-badge.finished {
    color: var(--success);
    background: color-mix(in oklab, var(--success) 15%, transparent);
  }

  .pulse {
    width: 0.5em;
    height: 0.5em;
    border-radius: 50%;
    background: currentColor;
    animation: pulse 1.6s ease-in-out infinite;
  }

  @keyframes pulse {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.25;
    }
  }

  .raw:hover {
    color: var(--primary);
    text-decoration: underline;
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

  .error {
    color: var(--destructive);
  }

  .muted {
    color: var(--muted-foreground);
  }

  .summary {
    display: flex;
    flex-wrap: wrap;
    gap: 1.5rem;
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
  }

  .label {
    font-size: 10.5px;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    color: var(--muted-foreground);
  }

  .value {
    font-size: 0.9rem;
  }

  .value.mono {
    font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
  }

  table.pages {
    width: 100%;
    max-width: 32rem;
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

  .chip {
    font-size: 0.7rem;
    font-weight: 500;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    background: var(--muted);
    color: var(--muted-foreground);
    width: fit-content;
  }

  .chip.success {
    background: color-mix(in oklab, var(--success) 15%, transparent);
    color: var(--success);
  }

  .chip.destructive {
    background: color-mix(in oklab, var(--destructive) 15%, transparent);
    color: var(--destructive);
  }

  details.pipeline-yaml {
    margin-bottom: 1.25rem;
  }

  details.pipeline-yaml summary {
    cursor: pointer;
    font-size: 0.85rem;
    color: var(--muted-foreground);
  }

  details.pipeline-yaml pre {
    margin: 0.5rem 0 0;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 0.75rem 1rem;
    font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
    font-size: 12px;
    white-space: pre-wrap;
  }

  .log {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }

  .group,
  .group summary,
  .log-msg,
  .log-line.full {
    font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
    font-size: 12px;
    white-space: pre-wrap;
  }

  .log-line {
    display: grid;
    grid-template-columns: auto auto 1fr;
    gap: 0 0.75ch;
    align-items: baseline;
  }

  .log-line.full {
    grid-column: 1 / -1;
  }

  .log-time {
    color: var(--muted-foreground);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }

  .log-level {
    font-size: 9.5px;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    font-weight: 500;
    padding: 0 0.35ch;
    border-radius: 3px;
    width: fit-content;
    background: var(--muted);
    color: var(--muted-foreground);
  }

  .log-level.warning {
    background: color-mix(in oklab, var(--warning) 18%, transparent);
    color: var(--warning);
  }

  .log-level.destructive {
    background: color-mix(in oklab, var(--destructive) 18%, transparent);
    color: var(--destructive);
  }

  .group.info {
    color: var(--foreground);
  }

  .group.model {
    color: var(--primary);
  }

  .group.warning {
    color: var(--warning);
    background: color-mix(in oklab, var(--warning) 10%, transparent);
    border-radius: 4px;
    padding: 0.1rem 0.35rem;
  }

  .group.error {
    color: var(--destructive);
    background: color-mix(in oklab, var(--destructive) 8%, transparent);
    border-radius: 4px;
    padding: 0.1rem 0.35rem;
  }

  details.group.http {
    color: var(--muted-foreground);
  }

  details.group.http summary {
    cursor: pointer;
    white-space: normal;
  }

  details.group.http .lines {
    margin: 0.15rem 0 0;
  }
</style>
