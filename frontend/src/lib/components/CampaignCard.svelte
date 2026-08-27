<script lang="ts">
  // One campaign: header (name, pipeline chip, counts) and its volume table.
  import {
    campaignHealth,
    pagesLabel,
    shortDate,
    viewerHref,
  } from "$lib/derive.js";
  import type { CampaignEntry } from "$lib/status.js";

  let { campaign: c }: { campaign: CampaignEntry } = $props();

  let collapsed = $state(false); // expanded by default
  let yamlOpen = $state(false); // collapsed by default

  // Stable ids for aria-controls; campaign names are unique in a document.
  const slug = $derived(c.name.replace(/[^a-zA-Z0-9_-]/g, "-"));
  const tableId = $derived(`volumes-${slug}`);
  const yamlId = $derived(`pipeline-${slug}`);

  function statusLabel(status: string): string {
    return status === "pending" ? "planned" : status;
  }

  const TERMINAL_HINTS: Record<string, string> = {
    "exit-13":
      "permanent failure (wrapper exit 13): parked until an operator clears the attempts record or bumps the pipeline id",
    capped:
      "attempt cap reached: parked until an operator clears the attempts record or bumps the pipeline id",
  };
  function terminalHint(reason: string): string {
    return (
      TERMINAL_HINTS[reason] ??
      `parked (${reason}): needs an operator to clear it`
    );
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
  <!-- Two sibling buttons, not a button inside a button: Enter on the
       pipeline chip opens the YAML and leaves the table alone. -->
  <div class="camp">
    <button
      type="button"
      class="camp-toggle"
      aria-expanded={!collapsed}
      aria-controls={c.error === null ? tableId : undefined}
      onclick={() => (collapsed = !collapsed)}
    >
      <span class="disclosure" aria-hidden="true">{collapsed ? "▸" : "▾"}</span>
      <span class="camp-name">{c.name}</span>
    </button>
    {#if c.error !== null}
      <span class="chip needs-attention">broken</span>
    {:else}
      {#if c.pipeline_yaml !== null}
        <button
          type="button"
          class="chip pipeline"
          aria-expanded={yamlOpen}
          aria-controls={yamlId}
          title={c.pipeline_steps !== null && c.pipeline_steps.length > 0
            ? c.pipeline_steps.join(" → ")
            : "show pipeline YAML"}
          onclick={() => (yamlOpen = !yamlOpen)}>{c.pipeline}</button
        >
      {:else}
        <span class="chip pipeline static">{c.pipeline}</span>
      {/if}
      <span class="counts">
        {c.totals.done}/{c.totals.total} volumes
        {#if pagesLabel(c.totals) !== null}
          <span class="pages">· {pagesLabel(c.totals)}</span>
        {/if}
      </span>
    {/if}
  </div>
  {#if c.error !== null}<p class="notice error-row">{c.error}</p>{/if}
  {#if yamlOpen && c.pipeline_yaml !== null}
    <pre class="pipeline-yaml" id={yamlId}>{c.pipeline_yaml}</pre>
  {/if}
  {#if c.orphans.length > 0}
    <p class="notice warn-row">
      orphaned results (in bucket, not in git): {c.orphans.join(", ")}
    </p>
  {/if}
  {#if !collapsed && c.error === null}
    <div class="table-scroll" id={tableId}>
      <table class="volumes">
        <caption class="sr-only">Volumes in campaign {c.name}</caption>
        <colgroup>
          <col class="c-thumb" />
          <col class="c-vid" />
          <col class="c-status" />
          <col class="c-num" />
          <col class="c-num c-attempts" />
          <col class="c-updated" />
          <col class="c-links" />
        </colgroup>
        <thead>
          <tr>
            <th></th>
            <th>volume</th>
            <th>status</th>
            <th class="num">pages</th>
            <th class="num c-attempts">attempts</th>
            <th class="c-updated">updated</th>
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
                {#if v.terminal !== null}
                  <!-- Sticky: the reconciler will not resubmit until an
                     operator clears the attempts record or bumps the
                     pipeline id. -->
                  <span class="terminal" title={terminalHint(v.terminal)}
                    >{v.terminal}</span
                  >
                {/if}
              </td>
              <td class="num">
                {v.pages_total !== null || v.pages_done !== null
                  ? `${v.pages_done ?? 0}/${v.pages_total ?? "?"}`
                  : "—"}
              </td>
              <td class="num c-attempts">{v.attempts > 0 ? v.attempts : "—"}</td
              >
              <td class="updated c-updated">
                {#if v.updated !== null && shortDate(v.updated) !== null}
                  <time datetime={v.updated} title={v.updated}
                    >{shortDate(v.updated)}</time
                  >
                {:else}
                  —
                {/if}
              </td>
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
                      <a href={v.source_manifest} target="_blank" rel="noopener"
                        >source</a
                      >
                    {:else}
                      <span
                        class="invalid-url"
                        title="source_manifest is not an http(s) URL"
                        >invalid url</span
                      >
                    {/if}
                  </span>
                  <span class="slot">
                    {#if v.failure_log !== null}
                      <a
                        class="danger"
                        href={v.failure_log}
                        target="_blank"
                        rel="noopener">log</a
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
    </div>
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

  .camp-toggle:focus-visible,
  .chip.pipeline:focus-visible {
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
    color: var(--on-strong);
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

  /* Narrow screens: the table scrolls inside its own container (the page
     never scrolls sideways) and drops attempts/updated, the two columns a
     phone reader is least likely to be after. */
  .table-scroll {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
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

  /* After the col rules so the narrow widths win the cascade. The table
     keeps a floor of 30rem and scrolls in .table-scroll; attempts/updated
     go, the other fixed columns tighten so the volume name keeps ~10rem. */
  @media (max-width: 48rem) {
    table.volumes {
      min-width: 32rem;
    }

    .c-attempts,
    .c-updated {
      display: none;
    }

    col.c-num {
      width: 3.6rem;
    }

    col.c-links {
      width: 9.6rem;
    }

    td.links .slot {
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
    background: var(--success-soft);
  }

  .status.running {
    color: var(--primary);
    background: var(--primary-soft);
  }

  .status.queued,
  .status.retry,
  .status.deleting {
    color: var(--warning);
    background: var(--warning-soft);
  }

  .status.needs-attention,
  .status.unreachable,
  .status.unsupported {
    color: var(--destructive);
    background: var(--destructive-soft);
  }

  .status.pending,
  .status.unknown {
    color: var(--muted-foreground);
    background: var(--muted);
  }

  .terminal {
    display: inline-block;
    margin-left: 0.3rem;
    font-size: 10.5px;
    font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
    color: var(--destructive);
    border: 1px solid var(--destructive);
    border-radius: 3px;
    padding: 0 0.3rem;
    line-height: 1.4;
    cursor: help;
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
    min-width: 3.3rem;
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
