<script lang="ts">
  // One campaign = one Indexed Job. The header is the JobSummary the parent
  // already has (from GET /api/v1/jobs); the volume table is fetched
  // separately and paged (GET /api/v1/jobs/{ns}/{name}?offset&limit), since
  // a campaign can carry thousands of volumes. No thumbnails: the read API
  // has no per-volume image, only the finished results.
  import {
    ApiUnreachable,
    fetchJob,
    shortDate,
    type JobSummary,
    type VolumeView,
  } from "$lib/api.js";
  import { RELOAD_MS } from "$lib/config.js";

  let { job }: { job: JobSummary } = $props();

  let collapsed = $state(false); // expanded by default
  let volumes = $state<VolumeView[]>([]);
  let detailError = $state<string | null>(null);
  let loadingMore = $state(false);

  const PAGE = 200;

  // Stable id for aria-controls; namespace/name is unique per Job.
  const slug = $derived(
    `${job.namespace}-${job.name}`.replace(/[^a-zA-Z0-9_-]/g, "-"),
  );
  const tableId = $derived(`volumes-${slug}`);

  // The card's left accent: worst-first, same intent as the old
  // volume-derived campaignHealth but read straight off the Job phase now
  // that the API computes it server-side.
  const health = $derived(
    job.phase === "Failed" || job.counts.failed > 0
      ? "failed"
      : job.phase === "Running"
        ? "active"
        : job.phase === "Succeeded"
          ? "done"
          : "idle",
  );

  const hasMore = $derived(volumes.length < job.counts.total);

  // reset=true replaces the table (the poll tick); reset=false appends the
  // next page (the "load more" button). A poll always collapses back to the
  // first page — simpler than tracking how many pages were expanded, and
  // the common case (a handful of volumes) never notices.
  async function load(reset: boolean): Promise<void> {
    try {
      const offset = reset ? 0 : volumes.length;
      const detail = await fetchJob(job.namespace, job.name, offset, PAGE);
      volumes = reset ? detail.volumes : [...volumes, ...detail.volumes];
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
    void load(true);
    const timer = setInterval(() => void load(true), RELOAD_MS);
    return () => clearInterval(timer);
  });

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

<section class="campaign" data-health={health}>
  <div class="camp">
    <button
      type="button"
      class="camp-toggle"
      aria-expanded={!collapsed}
      aria-controls={tableId}
      onclick={() => (collapsed = !collapsed)}
    >
      <span class="disclosure" aria-hidden="true">{collapsed ? "▸" : "▾"}</span>
      <span class="camp-name">{job.namespace}/{job.name}</span>
    </button>
    <span class="chip pipeline">{job.pipeline}</span>
    <span class="chip phase {job.phase.toLowerCase()}">{job.phase}</span>
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
              <td class="links">
                <span class="slot">
                  {#if v.state === "done"}
                    <a
                      href={"uv.html#?manifest=" + v.iiifUrl}
                      target="_blank"
                      rel="noopener">open</a
                    >
                  {/if}
                </span>
                <span class="slot">
                  <a href={logHref(v)}>log</a>
                </span>
              </td>
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
  .chip.phase.paused {
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

  col.c-links {
    width: 8rem;
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

  td.links {
    white-space: nowrap;
  }

  td.links .slot {
    display: inline-block;
    min-width: 2.9rem;
  }

  td.links a {
    color: var(--primary);
    text-decoration: none;
  }

  td.links a:hover {
    text-decoration: underline;
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
