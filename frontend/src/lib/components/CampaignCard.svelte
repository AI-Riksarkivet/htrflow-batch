<script lang="ts">
  // One campaign = one Indexed Job. The header is the JobSummary the parent
  // already has (from GET /api/v1/jobs); the volume table is fetched
  // separately and paged (GET /api/v1/jobs/{ns}/{name}?offset&limit), since
  // a campaign can carry thousands of volumes. No thumbnails: the read API
  // has no per-volume image, only the finished results.
  import {
    ApiUnreachable,
    fetchJob,
    isHttpUrl,
    shortDate,
    type JobSummary,
    type VolumeView,
  } from "$lib/api.js";
  import { RELOAD_MS } from "$lib/config.js";
  import { untrack } from "svelte";

  let { job }: { job: JobSummary } = $props();

  // Cards start folded: the page is a list of campaigns to scan, not a wall
  // of volume tables. The choice is remembered per campaign; every storage
  // access is wrapped, because a browser can refuse it (private mode, a
  // file:// page, cookies-blocked) and that is not an error worth showing.
  const memoryKey = $derived(`htrflow.card.${job.namespace}/${job.name}`);

  function remembered(): boolean {
    try {
      return localStorage.getItem(memoryKey) !== "open";
    } catch {
      return true;
    }
  }

  let collapsed = $state(remembered());

  function toggle(): void {
    collapsed = !collapsed;
    try {
      localStorage.setItem(memoryKey, collapsed ? "closed" : "open");
    } catch {
      // Nothing to remember it with; the card still opens and closes.
    }
  }
  let yamlOpen = $state(false); // collapsed by default
  let volumes = $state<VolumeView[]>([]);
  let failures = $state<VolumeView[]>([]);
  let latest = $state<VolumeView | null>(null);
  let pipelineSteps = $state<string[]>([]);
  let pipelineYaml = $state("");
  let detailError = $state<string | null>(null);
  let loadingMore = $state(false);

  const PAGE = 200;
  // The API refuses a larger limit (packages/web app.py, `le=1000`), so a
  // card with more than five pages open refreshes the first five and keeps
  // the rest as last fetched.
  const MAX_LIMIT = 1000;

  // Stable id for aria-controls; namespace/name is unique per Job.
  const slug = $derived(
    `${job.namespace}-${job.name}`.replace(/[^a-zA-Z0-9_-]/g, "-"),
  );
  const tableId = $derived(`volumes-${slug}`);
  const yamlId = $derived(`pipeline-${slug}`);

  // The card's left accent: worst-first, same intent as the old
  // volume-derived campaignHealth but read straight off the Job phase now
  // that the API computes it server-side.
  const health = $derived(
    job.phase === "Failed" ||
      job.phase === "PartiallyFailed" ||
      job.counts.failed > 0
      ? "failed"
      : job.phase === "Running"
        ? "active"
        : job.phase === "Succeeded"
          ? "done"
          : "idle",
  );

  const hasMore = $derived(volumes.length < job.counts.total);

  // Every other phase is already a word; this one is two.
  const phaseLabel = $derived(
    job.phase === "PartiallyFailed" ? "partially failed" : job.phase,
  );

  // reset=true replaces the table (the poll tick); reset=false appends the
  // next page (the "load more" button). A poll re-fetches every page that is
  // currently open, rounded up to whole pages, so a tick does not undo
  // "load more" under the reader's cursor; counts.total still ends paging.
  async function load(reset: boolean): Promise<void> {
    try {
      const offset = reset ? 0 : volumes.length;
      const limit = reset
        ? Math.min(
            MAX_LIMIT,
            Math.max(PAGE, Math.ceil(volumes.length / PAGE) * PAGE),
          )
        : PAGE;
      const detail = await fetchJob(job.namespace, job.name, offset, limit);
      // A short answer is the whole list, so it replaces what is loaded; a
      // full one may have more behind it, and those rows stay as they were
      // rather than vanishing under the reader.
      const refreshed =
        detail.volumes.length === limit
          ? [...detail.volumes, ...volumes.slice(limit)]
          : detail.volumes;
      volumes = reset ? refreshed : [...volumes, ...detail.volumes];
      // Not paged by the API (up to 50 newest failed-with-a-reason rows,
      // independent of offset/limit) — refreshed on every call.
      failures = detail.failures;
      // Also computed over every volume, so it is right for a campaign whose
      // in-flight index is far past the loaded page.
      latest = detail.latest;
      pipelineSteps = detail.pipelineSteps;
      pipelineYaml = detail.pipelineYaml;
      detailError = null;
    } catch (e) {
      detailError =
        e instanceof ApiUnreachable ? e.message : "invalid API response";
    }
  }

  async function loadMore(): Promise<void> {
    loadingMore = true;
    await load(false);
    loadingMore = false;
  }

  $effect(() => {
    // untrack: load() reads `volumes` to size its refresh and then writes it,
    // and an effect that reads its own output re-runs forever. Nothing here
    // needs re-subscribing anyway — the list keys each card by
    // namespace/name, so a card never changes campaign under its own feet.
    untrack(() => void load(true));
    const timer = setInterval(() => void load(true), RELOAD_MS);
    return () => clearInterval(timer);
  });

  // Defence in depth. `sourceUrl` is the API's copy of a line from a
  // campaign's volumes.txt, which is a file humans edit in a git repo — so
  // it is checked here too, at the last step before it becomes an href, the
  // way the old card checked every URL the status document carried. Anything
  // but an absolute http(s) URL is no link at all.
  function sourceOf(v: VolumeView): string | null {
    return v.sourceUrl !== null && isHttpUrl(v.sourceUrl) ? v.sourceUrl : null;
  }

  // The finished result once there is one, the source manifest before that
  // (the old derive.viewerHref) — so "open" is a live link from the first
  // tick, not only after the volume publishes.
  function openHref(v: VolumeView): string | null {
    const manifest = v.state === "done" ? v.iiifUrl : sourceOf(v);
    return manifest === null ? null : `uv.html#?manifest=${manifest}`;
  }

  // manifest carries manifestUrl so /log's RunSummaryCard has something to
  // render; live=1 for a volume still in flight, so /log re-fetches on the
  // wrapper's log-ship cadence instead of showing a static snapshot.
  function logHref(v: VolumeView): string {
    return (
      "log?log=" +
      encodeURIComponent(v.logUrl) +
      "&manifest=" +
      encodeURIComponent(v.manifestUrl) +
      (v.state !== "done" ? "&live=1" : "")
    );
  }
</script>

<!-- Three fixed slots (open · source · log) so a missing link leaves a gap
     instead of shifting its neighbours; the eye can scan a column of
     "source" straight down. One snippet, so the folded strip and the table
     row can never drift apart. -->
{#snippet links(v: VolumeView)}
  {@const open = openHref(v)}
  {@const source = sourceOf(v)}
  <span class="slot">
    {#if open !== null}
      <a href={open} target="_blank" rel="noopener">open</a>
    {/if}
  </span>
  <span class="slot">
    {#if source !== null}
      <a href={source} target="_blank" rel="noopener">source</a>
    {/if}
  </span>
  <span class="slot">
    <a href={logHref(v)}>log</a>
  </span>
{/snippet}

<section class="campaign" data-health={health}>
  <div class="camp">
    <!-- aria-controls only while the table exists: it must be an IDREF that
         resolves, and a folded card renders no table (the pre-Task-7 card
         dropped the attribute the same way when it had none to point at).
         aria-expanded alone carries the open/closed state, and aria-controls
         is optional in the disclosure pattern; rendering an empty element
         just to keep the id would be worse — the reference would resolve to
         nothing at all. -->
    <button
      type="button"
      class="camp-toggle"
      aria-expanded={!collapsed}
      aria-controls={collapsed ? undefined : tableId}
      onclick={toggle}
    >
      <span class="disclosure" aria-hidden="true">{collapsed ? "▸" : "▾"}</span>
      <span class="camp-name">{job.namespace}/{job.name}</span>
    </button>
    <!-- Two sibling buttons, not a button inside a button: Enter on the
         pipeline chip opens the YAML and leaves the table alone. Static
         until the detail has loaded (or when the pipeline ConfigMap is
         gone): there is nothing to toggle yet. -->
    {#if pipelineYaml !== ""}
      <button
        type="button"
        class="chip pipeline"
        aria-expanded={yamlOpen}
        aria-controls={yamlId}
        title={pipelineSteps.length > 0
          ? pipelineSteps.join(" → ")
          : "show pipeline YAML"}
        onclick={() => (yamlOpen = !yamlOpen)}>{job.pipeline}</button
      >
    {:else}
      <span class="chip pipeline static">{job.pipeline}</span>
    {/if}
    <span class="chip phase {job.phase.toLowerCase()}">{phaseLabel}</span>
    <span class="counts">
      {job.counts.done}/{job.counts.total} volumes
      {#if job.counts.failed > 0}
        <span class="bad"> · {job.counts.failed} failed</span>
      {/if}
      {#if job.counts.active > 0}
        <span> · {job.counts.active} active</span>
      {/if}
    </span>
  </div>
  {#if yamlOpen && pipelineYaml !== ""}
    <pre class="pipeline-yaml" id={yamlId}>{pipelineYaml}</pre>
  {/if}
  {#if job.createdAt !== null}
    <p class="meta">
      created <time datetime={job.createdAt} title={job.createdAt}
        >{shortDate(job.createdAt) ?? job.createdAt}</time
      >
    </p>
  {/if}
  {#if detailError !== null}
    <p class="notice error-row" role="alert">
      Cannot load volumes: {detailError}
    </p>
  {/if}
  {#if collapsed && latest !== null}
    <p class="latest">
      <span class="latest-state {latest.state}">{latest.state}</span>
      <span class="latest-id" title={latest.id}>{latest.id}</span>
      <span class="links">{@render links(latest)}</span>
    </p>
  {/if}
  {#if failures.length > 0}
    <div class="failures">
      <p class="failures-heading">failures ({failures.length})</p>
      <ul class="failures-list">
        {#each failures as f (f.id)}
          <li>
            <a class="failure-link" href={logHref(f)}>
              <span class="fid">{f.id}</span> —
              <span class="reason">{f.reason}</span>
            </a>
          </li>
        {/each}
      </ul>
    </div>
  {/if}
  {#if !collapsed}
    <div class="table-scroll" id={tableId}>
      <table class="volumes">
        <caption class="sr-only">Volumes in campaign {job.name}</caption>
        <colgroup>
          <col class="c-vid" />
          <col class="c-status" />
          <col class="c-links" />
        </colgroup>
        <thead>
          <tr>
            <th>volume</th>
            <th>status</th>
            <th>links</th>
          </tr>
        </thead>
        <tbody>
          {#each volumes as v (v.id)}
            <tr>
              <td class="vid">
                <span class="vid-name" title={v.id}>{v.id}</span>
                {#if v.reason !== undefined}
                  <span class="verr">{v.reason}</span>
                {/if}
              </td>
              <td>
                <span class="status {v.state}">
                  <span class="dot"></span>
                  {v.state}
                </span>
              </td>
              <td class="links">{@render links(v)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
      {#if hasMore}
        <button
          type="button"
          class="load-more"
          disabled={loadingMore}
          onclick={loadMore}
        >
          {loadingMore
            ? "loading…"
            : `load more (${volumes.length}/${job.counts.total})`}
        </button>
      {/if}
    </div>
  {/if}
</section>

<style>
  /* The left accent is the campaign's health at a glance: green = every
     index done, blue = a Job still running, red = a Job failed or carries a
     failed index, grey = queued/paused/nothing moving. */
  .campaign {
    background: var(--card);
    border: 1px solid var(--border);
    border-left: 3px solid var(--muted-foreground);
    border-radius: var(--radius);
    padding: 0.6rem 0.75rem;
    margin-bottom: 0.5rem;
  }

  .campaign[data-health="done"] {
    border-left-color: var(--success);
  }

  .campaign[data-health="active"] {
    border-left-color: var(--primary);
  }

  .campaign[data-health="failed"] {
    border-left-color: var(--destructive);
  }

  .camp {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.35rem 0.75rem;
    padding: 0.3rem 0;
  }

  .camp-toggle {
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 0.75rem;
    font-size: 1rem;
    font-weight: 600;
    background: none;
    border: none;
    padding: 0;
    text-align: left;
    color: var(--foreground);
    font-family: inherit;
    min-width: 0;
  }

  .camp-toggle:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
    border-radius: 3px;
  }

  .disclosure {
    color: var(--muted-foreground);
    font-size: 0.75rem;
    width: 1em;
  }

  .camp-name {
    min-width: 0;
    overflow-wrap: anywhere;
  }

  .counts {
    margin-left: auto;
    color: var(--muted-foreground);
    font-size: 0.85rem;
    font-weight: 400;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }

  .bad {
    color: var(--destructive);
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

  .chip.pipeline {
    background: var(--primary-soft);
    color: var(--primary);
    cursor: pointer;
    border: none;
    font-family: inherit;
    line-height: 1.4;
  }

  .chip.pipeline.static {
    cursor: default;
  }

  .chip.pipeline:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
    border-radius: 3px;
  }

  pre.pipeline-yaml {
    margin: 0.5rem 0 0;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 0.75rem 1rem;
    font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
    font-size: 12px;
    white-space: pre-wrap;
  }

  .chip.phase.succeeded {
    background: var(--success-soft);
    color: var(--success);
  }

  .chip.phase.running {
    background: var(--primary-soft);
    color: var(--primary);
  }

  .chip.phase.queued,
  .chip.phase.paused,
  /* Warning, not error: some of the campaign did publish. */
  .chip.phase.partiallyfailed {
    background: var(--warning-soft);
    color: var(--warning);
  }

  .chip.phase.failed {
    background: var(--destructive);
    color: var(--on-strong);
  }

  .meta {
    color: var(--muted-foreground);
    font-size: 0.8rem;
    margin: 0.25rem 0 0;
  }

  .notice {
    font-size: 0.85rem;
    margin: 0.25rem 0 0;
  }

  .error-row {
    color: var(--destructive);
  }

  /* Compact, always-visible callout (independent of the collapsed volume
     table): the newest ≤50 failed-with-a-reason rows the API returns. */
  .failures {
    margin-top: 0.4rem;
    padding: 0.4rem 0.6rem;
    border: 1px solid var(--destructive);
    border-radius: var(--radius);
    background: var(--destructive-soft);
  }

  .failures-heading {
    margin: 0 0 0.2rem;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    color: var(--destructive);
  }

  .failures-list {
    list-style: none;
    margin: 0;
    padding: 0;
    font-size: 12px;
  }

  .failure-link {
    display: flex;
    gap: 0.4rem;
    align-items: baseline;
    min-width: 0;
    color: inherit;
    text-decoration: none;
  }

  .failure-link:hover .reason {
    text-decoration: underline;
  }

  .fid {
    flex-shrink: 0;
    font-weight: 500;
  }

  /* One line, ellipsised — the wrapper's own message can run long; no JS
     truncation, CSS only. */
  .reason {
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }

  table.volumes {
    width: 100%;
    table-layout: fixed;
    border-collapse: collapse;
    margin-top: 0.5rem;
    font-size: 12.5px;
    line-height: 1.35;
  }

  .table-scroll {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }

  col.c-status {
    width: 8rem;
  }

  /* Three slots at 3.3rem, as before Task 7 dropped "source". */
  col.c-links {
    width: 11rem;
  }

  /* After the col rules so the narrow widths win the cascade: on a phone
     the three slots would leave the volume name a few characters, so both
     fixed columns tighten instead (the old card's rule, restored with the
     third slot). */
  @media (max-width: 48rem) {
    col.c-status {
      width: 5rem;
    }

    col.c-links {
      width: 9.6rem;
    }

    .slot {
      min-width: 2.9rem;
    }
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

  table.volumes td {
    padding: 0.2rem 0.5rem;
    border-bottom: 1px solid var(--border);
    vertical-align: middle;
  }

  table.volumes tbody tr:last-child td {
    border-bottom: none;
  }

  td.vid {
    font-weight: 500;
    color: var(--foreground);
    overflow: hidden;
  }

  .vid-name {
    display: block;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* The wrapper's own termination message for this index, or the reason
     the detail fetch itself failed — wraps, never widens the column. */
  .verr {
    display: block;
    font-weight: 400;
    font-size: 11.5px;
    color: var(--destructive);
    overflow-wrap: anywhere;
    white-space: normal;
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
    background: var(--success-soft);
  }

  .status.active {
    color: var(--primary);
    background: var(--primary-soft);
  }

  .status.failed {
    color: var(--destructive);
    background: var(--destructive-soft);
  }

  .status.pending {
    color: var(--muted-foreground);
    background: var(--muted);
  }

  td.links,
  .latest .links {
    white-space: nowrap;
  }

  .slot {
    display: inline-block;
    min-width: 3.3rem;
  }

  td.links a,
  .latest a {
    color: var(--primary);
    text-decoration: none;
  }

  td.links a:hover,
  .latest a:hover {
    text-decoration: underline;
  }

  /* The folded card's one-line window on the campaign: the volume most
     likely to be wanted, with the same three links its table row has, so
     UV and the run log are one click away without unfolding. */
  .latest {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
    margin: 0.25rem 0 0;
    font-size: 12.5px;
    min-width: 0;
  }

  .latest-state {
    color: var(--muted-foreground);
    font-size: 10.5px;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    flex-shrink: 0;
  }

  .latest-state.active {
    color: var(--primary);
  }

  .latest-state.done {
    color: var(--success);
  }

  .latest-id {
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    font-weight: 500;
  }

  .latest .links {
    margin-left: auto;
  }

  .load-more {
    font: inherit;
    color: var(--foreground);
    background: var(--muted);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 0.15rem 0.75rem;
    margin-top: 0.5rem;
    cursor: pointer;
    font-size: 0.8rem;
  }

  .load-more:disabled {
    opacity: 0.6;
    cursor: default;
  }
</style>
