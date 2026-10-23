<script lang="ts">
  // /search?q=<words> — full-text search over the indexed demo pages (the
  // htr_demo campaigns' ALTO, in Solr). Live: typing searches after a short
  // pause (#lib/search.DEBOUNCE_MS), the pipeline facet and the pager act at
  // once, and the address bar stays bookmarkable. The page asks this
  // origin's /search/select, which the web front passes to Solr read-only
  // and behind the same login as everything else; all query-shaping and
  // answer-reading lives in #lib/search, tested in #lib/search.test.
  import { browser } from "$app/env";
  import { onMount } from "svelte";
  import AppHeader from "#lib/components/AppHeader.svelte";
  import { resolveResultsBase } from "#lib/config.js";
  import {
    altoUrl,
    viewerHref,
    buildParams,
    DEBOUNCE_MS,
    PAGE_ROWS,
    pageCount,
    pageOf,
    parseAnswer,
    QuerySyntaxError,
    snippetFor,
    type SearchAnswer,
  } from "#lib/search.js";
  import { errorDetail, goToLogin } from "#lib/session.js";

  function queryParam(name: string): string {
    if (!browser) return "";
    return new URLSearchParams(window.location.search).get(name) ?? "";
  }

  let q = $state(queryParam("q"));
  let selected = $state<string[]>([]); // pipeline facet selections
  let start = $state(0);
  let answer = $state<SearchAnswer | null>(null);
  let error = $state<string | null>(null);
  let syntaxHint = $state(false);
  let searching = $state(false);

  // The newest request wins: an answer for words already typed over must
  // never replace a newer one (responses race; keystrokes don't wait).
  let inFlight: AbortController | undefined;
  let debounce: ReturnType<typeof setTimeout> | undefined;

  const pages = $derived(answer === null ? 1 : pageCount(answer.numFound));
  const page = $derived(pageOf(start));
  // The facet lists what the unfiltered answer counted, plus anything
  // selected (a selection can fall to zero without vanishing under the
  // reader's cursor).
  const facetValues = $derived.by(() => {
    const counted = new Map(
      (answer?.pipelines ?? []).map((f) => [f.value, f.count]),
    );
    const all = new Set([...counted.keys(), ...selected]);
    return [...all]
      .sort()
      .map((value) => ({ value, count: counted.get(value) ?? 0 }));
  });

  function bookmark(): void {
    if (!browser) return;
    const url = new URL(window.location.href);
    if (q.trim() === "") url.searchParams.delete("q");
    else url.searchParams.set("q", q.trim());
    history.replaceState(null, "", url);
  }

  async function run(): Promise<void> {
    const words = q.trim();
    if (words === "") {
      answer = null;
      error = null;
      syntaxHint = false;
      bookmark();
      return;
    }
    inFlight?.abort();
    const ctrl = new AbortController();
    inFlight = ctrl;
    searching = true;
    error = null;
    syntaxHint = false;
    try {
      const res = await fetch(
        `/search/select?${buildParams({ words, pipelines: selected, start })}`,
        {
          cache: "no-cache",
          signal: ctrl.signal,
        },
      );
      if (res.status === 401) return goToLogin();
      if (res.status === 404) {
        error =
          "This deployment has no search index (the /search route is not configured).";
        return;
      }
      if (res.ok || res.status === 400) {
        try {
          answer = parseAnswer(res.status, await res.json());
        } catch (e) {
          if (e instanceof QuerySyntaxError) {
            syntaxHint = true;
            answer = null;
          } else {
            throw e;
          }
        }
      } else {
        error =
          (await errorDetail(res)) ??
          `The search service answered HTTP ${res.status}.`;
      }
      bookmark();
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      error =
        "The search page could not reach the service — try again in a moment.";
    } finally {
      if (inFlight === ctrl) searching = false;
    }
  }

  // Typing waits a beat; a facet click or a page turn does not.
  function onInput(): void {
    start = 0;
    clearTimeout(debounce);
    debounce = setTimeout(() => void run(), DEBOUNCE_MS);
  }

  function togglePipeline(value: string): void {
    selected = selected.includes(value)
      ? selected.filter((v) => v !== value)
      : [...selected, value];
    start = 0;
    void run();
  }

  function goTo(newStart: number): void {
    start = newStart;
    void run();
  }

  // Opened as /search?q=… (bookmarked, or sent from the campaigns page):
  // run once with what the address bar brought. Later typing is onInput's
  // job, so the field never searches twice for one keystroke.
  onMount(() => {
    if (q.trim() !== "") void run();
  });
</script>

<main>
  <!-- The one header every page wears; here its search field IS the
       search box, in the same place it has on the campaigns page. -->
  <AppHeader
    bind:query={q}
    onsearch={() => {
      clearTimeout(debounce);
      start = 0;
      void run();
    }}
    oninput={onInput}
  />

  {#if facetValues.length > 0}
    <fieldset class="facets">
      <legend>Pipelines</legend>
      {#each facetValues as f (f.value)}
        <label class:checked={selected.includes(f.value)}>
          <input
            type="checkbox"
            checked={selected.includes(f.value)}
            onchange={() => togglePipeline(f.value)}
          />
          {f.value} <span class="n">({f.count})</span>
        </label>
      {/each}
    </fieldset>
  {/if}

  {#if syntaxHint}
    <p class="banner error" role="alert">
      That query could not be parsed — the search box takes plain words, not
      query syntax (AND, quotes, wildcards are matched literally, longer words
      fuzzily).
    </p>
  {/if}
  {#if error !== null}
    <p class="banner error" role="alert">{error}</p>
  {/if}

  <div aria-live="polite">
    {#if answer !== null && error === null && !syntaxHint}
      <p class="count">
        {#if searching}
          Searching…
        {:else}
          {answer.numFound}
          {answer.numFound === 1 ? "page" : "pages"}{selected.length > 0
            ? ` in ${selected.length === 1 ? "pipeline" : "pipelines"} ${selected.join(", ")}`
            : ""}
        {/if}
      </p>
      {#if answer.hits.length === 0}
        <p class="empty">
          Nothing matched — the text keeps its historical spelling, so try the
          old form too (helsa, not hälsa).
        </p>
      {:else}
        <ul class="hits">
          {#each answer.hits as hit (hit.id)}
            {@const alto = altoUrl(resolveResultsBase(), hit)}
            {@const viewer = viewerHref(resolveResultsBase(), hit)}
            {@const snippet = snippetFor(hit, answer.highlighting)}
            <li class="hit">
              <div class="where">
                <strong>{hit.volume_s ?? hit.id}</strong>
                <span class="page-id">page {hit.page_s ?? "?"}</span>
                <span class="pipeline">{hit.pipeline_s ?? ""}</span>
              </div>
              {#if snippet !== null}
                <!-- Solr escapes the transcript and marks the match with
                     <em>; what is rendered here is that escaped snippet. -->
                <p class="snippet">{@html snippet}</p>
              {/if}
              <div class="links">
                {#if alto !== null}<a
                    href="/alto?src={encodeURIComponent(alto)}">text</a
                  >{/if}
                {#if viewer !== null}<a href={viewer}>viewer</a>{/if}
                {#if hit.image_url_s}<a
                    href={hit.image_url_s}
                    target="_blank"
                    rel="noopener">scan</a
                  >{/if}
              </div>
            </li>
          {/each}
        </ul>
        {#if pages > 1}
          <nav class="pager" aria-label="Search result pages">
            <button
              type="button"
              disabled={start === 0}
              onclick={() => goTo(Math.max(0, start - PAGE_ROWS))}
            >
              ← Newer
            </button>
            <span>page {page} of {pages}</span>
            <button
              type="button"
              disabled={page >= pages}
              onclick={() => goTo(start + PAGE_ROWS)}
            >
              Older →
            </button>
          </nav>
        {/if}
      {/if}
    {/if}
  </div>
</main>

<style>
  /* app.css's tokens and the campaign card's idiom, never local values:
     the same oklch set drives the dark theme, and a hit is one more card
     in the list the reader already knows. */
  /* The same quiet pill as "show older campaigns" and LogoutButton. */
  .pager button {
    font: inherit;
    color: var(--foreground);
    background: var(--muted);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 0.3rem 1rem;
    cursor: pointer;
  }
  .pager button:hover:not(:disabled) {
    border-color: var(--primary);
    color: var(--primary);
  }
  .pager button:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
  }
  .pager button:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .facets {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
    border: none;
    padding: 0;
    margin: 0 0 0.5rem;
    font-size: 0.9rem;
  }
  .facets legend {
    float: left;
    margin-right: 0.75rem;
    color: var(--muted-foreground);
  }
  .facets label {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    cursor: pointer;
  }
  .facets label.checked {
    font-weight: 600;
  }
  .facets input:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
  }
  .facets .n {
    color: var(--muted-foreground);
  }

  .count {
    color: var(--muted-foreground);
    margin: 0.5rem 0;
  }

  .hits {
    list-style: none;
    padding: 0;
  }

  /* One card in the campaign list's shape: --card fill, a 3px left edge
     that carries the accent. A hit has no health to show, so the edge
     sits one notch down (primary-soft) until the pointer asks for it. */
  .hit {
    background: var(--card);
    border: 1px solid var(--border);
    border-left: 3px solid var(--primary-soft);
    border-radius: var(--radius);
    padding: 0.6rem 0.75rem;
    margin-bottom: 0.5rem;
  }
  .hit:hover {
    border-left-color: var(--primary);
  }
  .where {
    display: flex;
    gap: 0.75rem;
    align-items: baseline;
    flex-wrap: wrap;
  }
  .page-id {
    color: var(--muted-foreground);
    font-size: 0.9em;
  }
  /* The pipeline as a chip: soft accent surface, accent text — the same
     badge idiom the tokens were picked for, and it scans like the facet
     it mirrors. */
  .pipeline {
    background: var(--primary-soft);
    color: var(--primary);
    font-size: 0.78em;
    font-weight: 600;
    padding: 0.08em 0.55em;
    border-radius: 999px;
  }
  .snippet {
    margin: 0.5rem 0;
    white-space: pre-line;
    line-height: 1.5;
  }
  /* The match, marked the way an editor marks a find result: the strong
     warning fill with --on-strong on top, so dark mode gets the bright
     amber chip and light mode the deep one — never a washed-out soft
     surface behind ordinary text. */
  .snippet :global(em) {
    font-style: normal;
    font-weight: 650;
    background: var(--warning);
    color: var(--on-strong);
    padding: 0 0.15em;
    border-radius: 3px;
  }
  .links {
    display: flex;
    gap: 1rem;
    font-size: 0.9em;
  }
  .links a {
    color: var(--primary);
    font-weight: 550;
  }
  .links a:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
    border-radius: 3px;
  }

  .pager {
    display: flex;
    align-items: center;
    gap: 1rem;
    justify-content: center;
    margin: 1rem 0;
    color: var(--muted-foreground);
  }
</style>
