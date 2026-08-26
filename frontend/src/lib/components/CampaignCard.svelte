<script lang="ts">
  // One campaign: header (name, pipeline chip, counts) and its volume table.
  import { campaignHealth, pagesLabel, shortDate, viewerHref } from "$lib/derive.js";
  import type { CampaignEntry } from "$lib/status.js";

  let { campaign: c }: { campaign: CampaignEntry } = $props();

  let collapsed = $state(false); // expanded by default
  let yamlOpen = $state(false); // collapsed by default

  function toggleYaml(event: Event): void {
    event.stopPropagation();
    yamlOpen = !yamlOpen;
  }
  function toggleYamlOnKey(event: KeyboardEvent): void {
    if (event.key !== "Enter") return;
    toggleYaml(event);
  }

  function statusLabel(status: string): string {
    return status === "pending" ? "planned" : status;
  }

  // A broken thumbnail becomes the same neutral square as a missing one.
  function placeholder(): HTMLSpanElement {
    const el = document.createElement("span");
    el.className = "thumb-placeholder";
    el.setAttribute("aria-hidden", "true");
    return el;
  }
</script>

<section
  class="campaign"
  data-health={c.error !== null ? "failed" : campaignHealth(c.volumes)}
>
  <button class="camp" onclick={() => (collapsed = !collapsed)}>
    <span class="disclosure">{collapsed ? "▸" : "▾"}</span>
    <span class="camp-name">{c.name}</span>
    {#if c.error !== null}
      <span class="chip needs-attention">broken</span>
    {:else}
      <span
        class="chip pipeline"
        role="button"
        tabindex="0"
        title={c.pipeline_steps !== null && c.pipeline_steps.length > 0
          ? c.pipeline_steps.join(" → ")
          : undefined}
        onclick={toggleYaml}
        onkeydown={toggleYamlOnKey}>{c.pipeline}</span
      >
      <span class="counts">
        {c.totals.done}/{c.totals.total} volumes
        {#if pagesLabel(c.totals) !== null}
          <span class="pages">· {pagesLabel(c.totals)}</span>
        {/if}
      </span>
    {/if}
  </button>
  {#if c.error !== null}<p class="notice error-row">{c.error}</p>{/if}
  {#if yamlOpen && c.pipeline_yaml}
    <pre class="pipeline-yaml">{c.pipeline_yaml}</pre>
  {/if}
  {#if c.orphans.length > 0}
    <p class="notice warn-row">
      orphaned results (in bucket, not in git): {c.orphans.join(", ")}
    </p>
  {/if}
  {#if !collapsed && c.error === null}
    <table class="volumes">
      <colgroup>
        <col class="c-thumb" />
        <col class="c-vid" />
        <col class="c-status" />
        <col class="c-num" />
        <col class="c-num" />
        <col class="c-updated" />
        <col class="c-links" />
      </colgroup>
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
        {#each c.volumes as v (v.id)}
          <tr class:planned={v.status === "pending"}>
            <td class="thumb">
              <!-- Decorative: the row is identified by its id. Low fetch
                   priority so eight thumbnails never race status.json or
                   the viewer; the reconciler sends sized IIIF URLs and null
                   for service-less manifests, which get a neutral square. -->
              {#if v.thumbnail !== null}
                <img
                  src={v.thumbnail}
                  alt=""
                  loading="lazy"
                  fetchpriority="low"
                  decoding="async"
                  width="26"
                  height="26"
                  onerror={(e) => e.currentTarget.replaceWith(placeholder())}
                />
              {:else}
                <span class="thumb-placeholder" aria-hidden="true"></span>
              {/if}
            </td>
            <td class="vid">
              <span class="vid-name" title={v.id}>{v.id}</span>
              {#if v.error !== null}
                <span class="verr">{v.error}</span>
              {/if}
            </td>
            <td>
              <span class="status {v.status}">
                <span class="dot"></span>
                {statusLabel(v.status)}
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
              <!-- Three fixed slots (open · source · log) so a missing
                   link leaves a gap instead of shifting its neighbours;
                   the eye can scan a column of "source" straight down. -->
              {#if v.invalid !== true}
              {@const open = viewerHref(v)}
              <span class="slot">
                {#if v.status === "done" && open !== null}
                  <a href={open} target="_blank" rel="noopener">open</a>
                {/if}
              </span>
              <span class="slot">
                {#if v.source_manifest !== null}
                  <a href={v.source_manifest} target="_blank" rel="noopener">source</a>
                {:else}
                  <span class="invalid-url" title="source_manifest is not an http(s) URL"
                    >invalid url</span
                  >
                {/if}
              </span>
              <span class="slot">
                {#if v.failure_log !== null}
                  <a class="danger" href={v.failure_log} target="_blank" rel="noopener"
                    >log</a
                  >
                {:else if v.run_log !== null}
                  <a
                    href={"log?log=" +
                      encodeURIComponent(v.run_log) +
                      (v.run_manifest !== null
                        ? "&manifest=" + encodeURIComponent(v.run_manifest)
                        : "") +
                      (v.status !== "done" ? "&live=1" : "")}>log</a
                  >
                {/if}
              </span>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</section>

<style>
  /* The left accent is the campaign's health at a glance (worst volume
     wins): green = everything published, blue = work in flight, red = a
     volume needs a human, grey = nothing running and nothing done. */
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

  /* Counts sit flush right so they line up across campaigns regardless of
     how long each name + pipeline chip is. */
  .counts {
    margin-left: auto;
    color: var(--muted-foreground);
    font-size: 0.85rem;
    font-weight: 400;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
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
    cursor: pointer;
  }

  .notice {
    font-size: 0.85rem;
    margin: 0.25rem 0 0;
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

  .error-row {
    color: var(--destructive);
  }

  .warn-row {
    color: var(--warning);
  }

  /* Fixed layout + explicit column widths: every campaign's table shares
     the same grid, so status/pages/links line up when scanning down the
     page instead of each table auto-sizing to its own content. Only the
     volume column flexes. */
  table.volumes {
    width: 100%;
    table-layout: fixed;
    border-collapse: collapse;
    margin-top: 0.5rem;
    font-size: 12.5px;
    line-height: 1.35;
  }

  col.c-thumb {
    width: 2.4rem;
  }

  col.c-status {
    width: 8rem;
  }

  col.c-num {
    width: 5rem;
  }

  col.c-updated {
    width: 8rem;
  }

  col.c-links {
    width: 11rem;
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
    padding-right: 0;
  }

  td.thumb img,
  td.thumb :global(.thumb-placeholder) {
    width: 1.6rem;
    height: 1.6rem;
    object-fit: cover;
    border-radius: 3px;
    display: block;
  }

  td.thumb :global(.thumb-placeholder) {
    background: var(--muted);
    border: 1px solid var(--border);
    box-sizing: border-box;
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

  /* The reconciler's error text for the volume (or the reason this row
     could not be parsed) — wraps, never widens the column. */
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

  .status.pending,
  .status.unknown {
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

  td.links .slot {
    display: inline-block;
    width: 3.3rem;
  }

  td.links a {
    color: var(--primary);
    text-decoration: none;
  }

  td.links a:hover {
    text-decoration: underline;
  }

  td.links a.danger {
    color: var(--destructive);
  }

  .invalid-url {
    color: var(--destructive);
    font-size: 11px;
  }
</style>
