<script lang="ts">
  import { browser } from "$app/environment";
  import RunSummaryCard from "$lib/components/RunSummaryCard.svelte";
  import ThemeToggle from "$lib/components/ThemeToggle.svelte";
  // LIVE_MS matches the wrapper's log-ship period (polling faster buys
  // nothing); LIVE_MAX_FAILURES stops a log that never appears from
  // spinning forever. Both documented in $lib/config.
  import { LIVE_MAX_FAILURES, LIVE_MS } from "$lib/config.js";
  import { isHttpUrl, shortDate } from "$lib/derive.js";
  import {
    isTerminalManifest,
    runManifestSchema,
    type RunManifest,
  } from "$lib/run.js";
  import {
    isTerminalLog,
    parseRunLog,
    splitLogLine,
    type LogGroup,
  } from "$lib/runlog.js";

  // The route is opened as /log?log=<url>&manifest=<url> on a prerendered,
  // client-only SPA (see +layout.ts) — there is no SvelteKit load function
  // here, just window.location once we're in the browser.
  function queryParam(name: string): string | null {
    if (!browser) return null;
    return new URLSearchParams(window.location.search).get(name);
  }

  // The query string is untrusted input: only absolute http(s) URLs are
  // fetched or linked. Anything else is treated as absent (and, for the log
  // itself, reported).
  function httpParam(name: string): string | null {
    const value = queryParam(name);
    return value !== null && isHttpUrl(value) ? value : null;
  }

  const rawLogUrl = queryParam("log");
  const logUrl = httpParam("log");
  const manifestUrl = httpParam("manifest");
  // live=1: the campaign table links a volume that is still in flight. The
  // wrapper re-uploads the log while it runs, so we re-fetch on its cadence
  // and stop once the wrapper's terminal line shows up (or the finished
  // manifest lands, or the log keeps failing).
  const startedLive = queryParam("live") === "1";

  let logText = $state<string | null>(null);
  let logError = $state<string | null>(null);
  let manifest = $state<RunManifest | null>(null);
  let live = $state(startedLive);
  /** ISO timestamp of the last content change (shown via shortDate). */
  let updatedAt = $state<string | null>(null);
  let failures = $state(0);
  // Follow the tail only while the reader is already at the bottom; a reader
  // who scrolled up to look at something must not be yanked back down.
  let stickToBottom = $state(true);

  // One request per resource in flight: a slow poll is abandoned when the
  // next starts (or the page goes away), so responses never land out of order.
  let logInflight: AbortController | null = null;
  let manifestInflight: AbortController | null = null;

  async function loadLog(): Promise<void> {
    if (logUrl === null) {
      logError =
        rawLogUrl === null
          ? "no log URL given"
          : "log URL must be an absolute http(s) URL";
      return;
    }
    logInflight?.abort();
    const controller = new AbortController();
    logInflight = controller;
    try {
      // no-cache (not no-store): the browser revalidates with the object's
      // ETag and gets a 304 when nothing changed, instead of re-pulling a
      // multi-MB log every poll.
      const res = await fetch(logUrl, {
        cache: "no-cache",
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const text = await res.text();
      logError = null;
      failures = 0;
      if (text !== logText) {
        logText = text;
        updatedAt = new Date().toISOString();
      }
      if (live && isTerminalLog(text)) live = false;
    } catch (e) {
      if (controller.signal.aborted) return;
      const message = e instanceof Error ? e.message : String(e);
      // A live volume's log may not exist yet (first upload pending) — keep
      // polling rather than freezing on the first 404, but not forever.
      failures += 1;
      if (!live) {
        logError = message;
      } else if (failures >= LIVE_MAX_FAILURES) {
        live = false;
        logError = `gave up after ${failures} failed polls (${message})`;
      }
    }
  }

  function onScroll(): void {
    stickToBottom =
      window.innerHeight + window.scrollY >= document.body.scrollHeight - 40;
  }

  async function loadManifest(): Promise<void> {
    if (manifestUrl === null) return;
    manifestInflight?.abort();
    const controller = new AbortController();
    manifestInflight = controller;
    try {
      const res = await fetch(manifestUrl, {
        cache: "no-cache",
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const parsed = runManifestSchema.safeParse(await res.json());
      if (parsed.success) {
        manifest = parsed.data;
        // The wrapper writes manifest.json once, at the end of the run.
        if (live && isTerminalManifest(parsed.data)) live = false;
      }
    } catch {
      // Missing/failed manifest fetch → skip the summary card gracefully;
      // the raw log still renders on its own.
    }
  }

  $effect(() => {
    void loadLog();
    void loadManifest();
    return () => {
      logInflight?.abort();
      manifestInflight?.abort();
    };
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
      requestAnimationFrame(() =>
        window.scrollTo(0, document.body.scrollHeight),
      );
    }
  });

  const parsed = $derived<{ groups: LogGroup[] } | null>(
    logText !== null ? parseRunLog(logText) : null,
  );

  // Tint for the per-line level chip. WARNING/ERROR/CRITICAL get the same
  // treatment as the existing group-level tinting; INFO/DEBUG stay neutral.
  function levelClass(level: string): string {
    if (level === "WARNING") return "warning";
    if (level === "ERROR" || level === "CRITICAL") return "destructive";
    return "muted";
  }
</script>

<svelte:window onscroll={onScroll} />

<main>
  <header class="page">
    <div class="title-block">
      <a class="back" href="/">← campaigns</a>
      <div class="title-row">
        <img class="logo" src="/ra.svg" alt="Riksarkivet" />
        <h1>Run log{manifest !== null ? ` · ${manifest.volume}` : ""}</h1>
      </div>
    </div>
    <div class="header-right">
      {#if startedLive}
        <!-- role=status: a polite live region, so the switch to "finished"
             and each update are announced without stealing focus. -->
        <span class="live-badge" class:finished={!live} role="status">
          {#if live}
            <span class="pulse" aria-hidden="true"></span>live
            {#if updatedAt !== null}
              · updated <time datetime={updatedAt} title={updatedAt}
                >{shortDate(updatedAt)}</time
              >
            {:else}
              · waiting for first upload
            {/if}
          {:else if updatedAt !== null}
            finished · <time datetime={updatedAt} title={updatedAt}
              >{shortDate(updatedAt)}</time
            >
          {:else}
            stopped
          {/if}
        </span>
      {/if}
      {#if logUrl !== null}
        <a class="raw" href={logUrl} target="_blank" rel="noopener">raw</a>
      {/if}
      <ThemeToggle />
    </div>
  </header>

  {#if manifest !== null}
    <RunSummaryCard {manifest} />

    {#if manifest.pipeline_yaml}
      <details class="pipeline-yaml">
        <summary>pipeline</summary>
        <pre>{manifest.pipeline_yaml}</pre>
      </details>
    {/if}
  {/if}

  <section class="log" aria-label="run log">
    {#if logError !== null}
      <p class="error" role="alert">Cannot load log: {logError}</p>
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
              >{g.lines.length} HTTP request{g.lines.length === 1
                ? ""
                : "s"}</summary
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
  h1 {
    font-size: 1.25rem;
    font-weight: 600;
    margin: 0;
    overflow-wrap: anywhere;
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
    min-width: 0;
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
    flex-wrap: wrap;
    align-items: center;
    gap: 0.75rem;
  }

  .raw {
    color: var(--muted-foreground);
    font-size: 0.85rem;
    text-decoration: none;
  }

  .raw:hover {
    color: var(--primary);
    text-decoration: underline;
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
    background: var(--primary-soft);
    white-space: nowrap;
  }

  .live-badge.finished {
    color: var(--success);
    background: var(--success-soft);
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

  /* app.css shortens every animation for reduced motion; this one is a
     pure attention pulse, so it goes entirely. */
  @media (prefers-reduced-motion: reduce) {
    .pulse {
      animation: none;
    }
  }

  .error {
    color: var(--destructive);
  }

  .muted {
    color: var(--muted-foreground);
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
    min-width: 0;
  }

  .group,
  .group summary,
  .log-msg,
  .log-line.full {
    font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
    font-size: 12px;
    white-space: pre-wrap;
    /* Unbroken URLs and paths wrap instead of widening the page. */
    overflow-wrap: anywhere;
  }

  .log-line {
    display: grid;
    /* minmax(0, 1fr): a 1fr track's min-content floor is the longest
       unbreakable token, which pushed the grid past the viewport. */
    grid-template-columns: auto auto minmax(0, 1fr);
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
    background: var(--warning-soft);
    color: var(--warning);
  }

  .log-level.destructive {
    background: var(--destructive-soft);
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
    background: var(--warning-soft);
    border-radius: 4px;
    padding: 0.1rem 0.35rem;
  }

  .group.error {
    color: var(--destructive);
    background: var(--destructive-soft);
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
