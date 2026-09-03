<script lang="ts">
  // /alto?src=<url> — a page's ALTO, read as text with a raw-XML toggle.
  // Reached from PagesTable's "view" link (and, once bookmarked, opened
  // directly): a prerendered, client-only page like /log, so the query
  // string is read from window.location, not a SvelteKit load function.
  import { browser } from "$app/environment";
  import ThemeToggle from "$lib/components/ThemeToggle.svelte";
  import { isHttpUrl } from "$lib/api.js";
  import { type AltoPage, parseAlto, prettyXml } from "$lib/alto.js";

  function queryParam(name: string): string | null {
    if (!browser) return null;
    return new URLSearchParams(window.location.search).get(name);
  }

  const rawSrc = queryParam("src");
  const src = rawSrc !== null && isHttpUrl(rawSrc) ? rawSrc : null;

  // The header's page name: the ALTO filename without its extension
  // (".../alto/0001.xml" -> "0001"); the full URL when that's empty.
  const pageName = (() => {
    if (src === null) return null;
    const last = src.split("/").pop();
    return last ? last.replace(/\.xml$/i, "") : src;
  })();

  let xmlText = $state<string | null>(null);
  let page = $state<AltoPage | null>(null);
  let error = $state<string | null>(null);
  let showRaw = $state(false);

  async function load(): Promise<void> {
    if (src === null) {
      error =
        rawSrc === null
          ? "No ALTO URL given. Open this page from a run viewer's alto column."
          : "The ALTO URL must be an absolute http(s) URL.";
      return;
    }
    try {
      const res = await fetch(src, { cache: "no-cache" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      xmlText = await res.text();
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      error = `Could not load the ALTO file: ${message}. Check that the raw link still works.`;
      return;
    }
    let parsed: AltoPage;
    try {
      parsed = parseAlto(xmlText);
    } catch {
      error = "This file is not valid XML — it cannot be an ALTO page.";
      return;
    }
    if (parsed.lines.length === 0) {
      error =
        "This ALTO file has no text lines to show. Open the raw XML to inspect it.";
      return;
    }
    page = parsed;
    error = null;
  }

  $effect(() => {
    void load();
  });

  const WC_BUCKETS = [
    { min: 0.9, cls: "high", label: "high (≥0.9)" },
    { min: 0.7, cls: "medium", label: "medium (0.7–0.9)" },
    { min: 0, cls: "low", label: "low (<0.7)" },
  ] as const;

  function wcClass(wc: number | null): string {
    if (wc === null) return "unknown";
    return (WC_BUCKETS.find((b) => wc >= b.min) ?? WC_BUCKETS[2]).cls;
  }

  function wcTitle(wc: number | null): string {
    return wc === null
      ? "confidence unknown"
      : `confidence ${(wc * 100).toFixed(0)}%`;
  }
</script>

<main>
  <header class="page">
    <h1>{pageName === null ? "ALTO" : `ALTO · ${pageName}`}</h1>
    <div class="header-right">
      {#if src !== null}
        <a class="raw" href={src} target="_blank" rel="noopener">raw</a>
      {/if}
      {#if xmlText !== null}
        <button
          type="button"
          class="toggle"
          aria-pressed={showRaw}
          onclick={() => (showRaw = !showRaw)}
        >
          {showRaw ? "text" : "raw XML"}
        </button>
      {/if}
      <ThemeToggle />
    </div>
  </header>

  {#if showRaw && xmlText !== null}
    <pre class="code-block">{prettyXml(xmlText)}</pre>
  {:else if error !== null}
    <p class="error" role="alert">{error}</p>
  {:else if page === null}
    <p class="muted">Loading…</p>
  {:else}
    <p class="legend">
      confidence:
      {#each WC_BUCKETS as b (b.cls)}
        <span class="chip {b.cls}">{b.label}</span>
      {/each}
      <span class="chip unknown">unknown</span>
    </p>
    <div class="lines">
      {#each page.lines as line, i (i)}
        <p class="line {wcClass(line.wc)}" title={wcTitle(line.wc)}>
          {line.text}
        </p>
      {/each}
    </div>
  {/if}
</main>

<style>
  h1 {
    overflow-wrap: anywhere;
  }

  .toggle {
    font: inherit;
    font-size: 0.8rem;
    color: var(--foreground);
    background: var(--muted);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 0.15rem 0.7rem;
    cursor: pointer;
  }

  .legend {
    font-size: 0.75rem;
    color: var(--muted-foreground);
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 1rem;
  }

  .chip.high,
  .line.high {
    background: var(--success-soft);
  }

  .chip.medium,
  .line.medium {
    background: var(--warning-soft);
  }

  .chip.low,
  .line.low {
    background: var(--destructive-soft);
  }

  .chip.high {
    color: var(--success);
  }

  .chip.medium {
    color: var(--warning);
  }

  .chip.low {
    color: var(--destructive);
  }

  .lines {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }

  .line {
    margin: 0;
    padding: 0.1rem 0.5rem;
    border-radius: 4px;
    overflow-wrap: anywhere;
  }
</style>
