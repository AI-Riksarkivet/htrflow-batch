<script lang="ts">
  // One campaign = one Indexed Job. The header is the JobSummary the parent
  // already has (from GET /api/v1/jobs); the volume table is fetched
  // separately and paged (GET /api/v1/jobs/{ns}/{name}?offset&limit), since
  // a campaign can carry thousands of volumes. No thumbnails: the read API
  // has no per-volume image, only the finished results.
  import {
    clockTime,
    fetchJob,
    isHttpUrl,
    sameDay,
    shortDate,
    type CampaignNotice,
    type JobSummary,
    type VolumeView,
  } from "$lib/api.js";
  import { RELOAD_MS } from "$lib/config.js";
  import { inTrouble } from "$lib/order.js";
  import { startPolling } from "$lib/poll.js";
  import { modelLabel, modelUrl, pipelineModels } from "$lib/pipeline.js";
  import {
    describeApiError,
    describeLastError,
    describeProgress,
    describeReason,
  } from "$lib/reasons.js";
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
  // Failures the reader cannot already see. With the table open, a failed
  // row on the loaded page is right there with its reason, so the callout
  // must not say it again (the product owner, 2026-09-08: "we don't need to
  // list a failure twice"). Folded, the table is out of sight and every
  // failure belongs in the callout.
  let unseenFailures = $derived(
    collapsed
      ? failures
      : failures.filter((f) => !volumes.some((v) => v.id === f.id)),
  );
  // The pages of the volumes this response covered, summed by the API. Not
  // in JobSummary: the list endpoint reads no volumes at all.
  let pages = $state<{ done: number; total: number }>({ done: 0, total: 0 });
  // What went wrong anywhere in the campaign: failed pages, errors, and the
  // most recent error with the run log it came from (the product owner,
  // 2026-09-08 — an exception in a log should show on the front page).
  let notice = $state<CampaignNotice>({
    pagesFailed: 0,
    errors: 0,
    lastError: null,
  });
  // Zone 3. One line, and only when something is wrong. It merges what used
  // to be two things: a chip in the header row that repeated counts the
  // numbers line already shows, and a bulleted callout below it that said
  // the same failures again (the product owner, 2026-09-16). What is left
  // is the sentences and nothing else -- why the warm-up could not run, why
  // each volume failed, and the last page error -- in the order a reader
  // needs them.
  const lastErrorText = $derived(describeLastError(notice.lastError));
  const problems = $derived([
    ...(job.warmup.reason
      ? [{ id: "warm-up", text: describeReason(job.warmup.reason), href: null }]
      : []),
    // Each failed volume keeps its own run log, the way the callout this
    // line replaced did: a failure a reader can read about but not open is
    // half a message (2026-09-16 review). The id is the link, so the line
    // stays one line.
    ...unseenFailures.map((f) => ({
      id: f.id,
      text:
        f.reason === undefined
          ? "Failed, with no message from the pod."
          : describeReason(f.reason),
      href: logHref(f),
    })),
    ...(lastErrorText === null
      ? []
      : [{ id: null, text: lastErrorText, href: null }]),
  ]);
  const problemsText = $derived(
    problems
      .map((p) => (p.id === null ? p.text : `${p.id}: ${p.text}`))
      .join(" · "),
  );
  let pipelineSteps = $state<string[]>([]);
  let pipelineYaml = $state("");
  let detailError = $state<string | null>(null);
  let loadingMore = $state(false);

  // Which rows' page counts moved on the last poll, so only those flash.
  // First paint is not news: a volume is only in here once it has been seen
  // with a different `done`, which is why the counts are remembered rather
  // than compared against the rendered text.
  const seen = new Map<string, number>();
  let moved = $state(new Set<string>());

  function markMoved(rows: VolumeView[]): void {
    const now = new Set<string>();
    for (const v of rows) {
      const done = v.progress?.done;
      if (done === undefined) continue;
      const before = seen.get(v.id);
      if (before !== undefined && before !== done) now.add(v.id);
      seen.set(v.id, done);
    }
    moved = now;
  }

  // Zone 2. The two fractions a campaign has -- volumes and pages -- and the
  // error count, in fixed columns so ten cards line up like a table. A
  // total of zero is a total nobody knows yet (the API reads page counts
  // out of the bucket, and a pending campaign has nothing there): the cell
  // shows its label and an em dash rather than a bar of nothing over
  // nothing.
  const volumeCell = $derived({
    label: "volumes",
    done: job.counts.done,
    total: job.counts.total,
    failed: job.counts.failed,
    // Only while the Job is there to be running them: a reaped campaign's
    // record can still carry a count of volumes that were in flight when
    // anyone last looked (2026-09-16 review).
    active: job.phase === "Running" ? job.counts.active : 0,
  });
  const pageCell = $derived({
    label: "pages",
    done: pages.done,
    total: pages.total,
    failed: notice.pagesFailed,
  });

  /** The figures beside a bar: `2 / 4 · 2 failed`, or `—` when unknown. */
  function figures(cell: { done: number; total: number; failed: number }) {
    return cell.total > 0 ? `${cell.done} / ${cell.total}` : "—";
  }

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

  // A volume that finished but lost pages. It is not a failure — the volume
  // published — and it is not a clean run either, and the green chip made
  // the loss easy to miss with "1 failed" buried in the progress line beside
  // it (the product owner, 2026-09-15). Amber: the colour the phase chip
  // already gives a campaign that published some of itself and not the rest.
  function lostPages(v: VolumeView): number {
    return v.state === "done" ? (v.progress?.failed ?? 0) : 0;
  }

  // The colour must not be the only thing saying it (WCAG 1.4.1), so this
  // sentence is both the tooltip and what a screen reader reads instead of
  // the bare word.
  function doneWith(failed: number): string {
    return `done with ${failed} failed page${failed === 1 ? "" : "s"}`;
  }

  // The campaign-wide version: every index published, and pages were lost
  // along the way. `counts.failed > 0` is already a failure below.
  const campaignLost = $derived(
    job.phase === "Succeeded" && notice.pagesFailed > 0,
  );

  // The card's left accent: worst-first, read straight off the Job phase now
  // that the API computes it server-side. `inTrouble` is $lib/order's --
  // the same question the campaign list sorts by, so the accent and the
  // order can never disagree about what is wrong (2026-09-16 review).
  const health = $derived(
    inTrouble(job)
      ? "failed"
      : job.phase === "Running"
        ? "active"
        : campaignLost
          ? "lost"
          : job.phase === "Succeeded"
            ? "done"
            : "idle",
  );

  // "warm-up pending" / "warm-up running" / "warm-up failed" / "no warm-up";
  // null (no chip) once the warm-up has succeeded.
  const warmupChip = $derived(
    job.warmup.phase === "succeeded"
      ? null
      : job.warmup.phase === "missing"
        ? "no warm-up"
        : `warm-up ${job.warmup.phase}`,
  );

  // The dot means work is actually happening. `Running` is the projection's
  // fallback phase, so a campaign whose warm-up is still pending -- or was
  // never created -- reads as Running while nothing runs at all; the warm-up
  // chip beside it is the story there, and a beating dot would contradict it.
  const beating = $derived(
    job.phase === "Running" && job.warmup.phase === "succeeded",
  );

  // The models the pipeline loads, in step order — the campaign's provenance
  // in one line, matching the model block every ALTO it publishes carries.
  const models = $derived(pipelineModels(pipelineYaml));

  const hasMore = $derived(volumes.length < job.counts.total);

  // Every other phase is already a word; these two are not.
  const phaseLabel = $derived(
    job.phase === "PartiallyFailed"
      ? "partially failed"
      : job.phase === "Unknown"
        ? "outcome unknown"
        : job.phase,
  );

  // reset=true replaces the table (the poll tick); reset=false appends the
  // next page (the "load more" button). A poll re-fetches every page that is
  // currently open, rounded up to whole pages, so a tick does not undo
  // "load more" under the reader's cursor; counts.total still ends paging.
  async function load(reset: boolean, signal?: AbortSignal): Promise<boolean> {
    try {
      const offset = reset ? 0 : volumes.length;
      const limit = reset
        ? Math.min(
            MAX_LIMIT,
            Math.max(PAGE, Math.ceil(volumes.length / PAGE) * PAGE),
          )
        : PAGE;
      const detail = await fetchJob(
        job.namespace,
        job.name,
        offset,
        limit,
        signal,
      );
      if (signal?.aborted) return true;
      // A short answer is the whole list, so it replaces what is loaded; a
      // full one may have more behind it, and those rows stay as they were
      // rather than vanishing under the reader.
      const refreshed =
        detail.volumes.length === limit
          ? [...detail.volumes, ...volumes.slice(limit)]
          : detail.volumes;
      volumes = reset ? refreshed : [...volumes, ...detail.volumes];
      // Polls only. "Load more" appends rows without new counts for the ones
      // already on screen, so marking there would clear a highlight
      // mid-fade; the appended rows are seeded by the next poll instead, and
      // a row nobody has seen before cannot have moved anyway.
      if (reset) markMoved(volumes);
      // Not paged by the API (up to 50 newest failed-with-a-reason rows,
      // independent of offset/limit) — refreshed on every call.
      failures = detail.failures;
      // Also computed over every volume, so it is right for a campaign whose
      // in-flight index is far past the loaded page.
      latest = detail.latest;
      pages = { done: detail.pagesDone, total: detail.pagesTotal };
      notice = {
        pagesFailed: detail.pagesFailed,
        errors: detail.errors,
        lastError: detail.lastError,
      };
      pipelineSteps = detail.pipelineSteps;
      pipelineYaml = detail.pipelineYaml;
      detailError = null;
      return true;
    } catch (e) {
      if (signal?.aborted) return true;
      // One sentence, never the transport detail or a ZodError: what the
      // reader can do about it is the point ($lib/reasons).
      detailError = describeApiError(e, volumes.length > 0);
      return false;
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
    // $lib/poll is what keeps a page of cards from each queueing up requests
    // against a slow API, and from polling at all while nobody is looking.
    return untrack(() =>
      startPolling((signal) => load(true, signal), RELOAD_MS),
    );
  });

  /**
   * The fill's width, clamped to the track. `done` and `total` come from the
   * wrapper's progress.json in the results bucket — a document that arrives
   * over the network, and one a half-written run can make disagree with
   * itself — so a bar 900% wide or running backwards was a page the numbers
   * could ask for (2026-09-14 audit).
   */
  function pct(done: number, total: number): string {
    const ratio = total > 0 ? done / total : 0;
    return `${(Math.min(Math.max(ratio, 0), 1) * 100).toFixed(1)}%`;
  }

  /** The same clamp for the value a screen reader is told. */
  function clamp(done: number, total: number): number {
    return Math.min(Math.max(done, 0), Math.max(total, 0));
  }

  // Zone 1's right end: `14 Sept, 10:56 → 11:00`, the campaign's creation
  // and its finish. The arrow carries the "and then", which "created … /
  // finished …" needed two words for; the words themselves are still there
  // for a screen reader, since an arrow is decoration to one. A run that
  // finished the same day shows only the clock for its end. Only a campaign
  // that is RUNNING gets the open-ended form -- one that has not started
  // has nothing on the other side of the arrow to point at, and pointing
  // anyway read as though it were running (2026-09-16 review).
  const stillGoing = $derived(
    job.phase === "Running" && job.finishedAt === null,
  );
  const endsSameDay = $derived(
    job.createdAt !== null &&
      job.finishedAt !== null &&
      sameDay(job.createdAt, job.finishedAt),
  );
  const finishedLabel = $derived(
    job.finishedAt === null
      ? null
      : ((endsSameDay
          ? clockTime(job.finishedAt)
          : shortDate(job.finishedAt)) ?? job.finishedAt),
  );

  // Defence in depth. `sourceUrl` is the API's copy of a line from a
  // campaign's volumes.txt, which is a file humans edit in a git repo — so
  // it is checked here too, at the last step before it becomes an href, the
  // way the old card checked every URL the status document carried. Anything
  // but an absolute http(s) URL is no link at all.
  function sourceOf(v: VolumeView): string | null {
    return v.sourceUrl !== null && isHttpUrl(v.sourceUrl) ? v.sourceUrl : null;
  }

  // The published result once there is one, the source manifest before that
  // (the old derive.viewerHref) — so "open" is a live link from the first
  // tick, not only after the volume publishes. Switches on viewerPublished,
  // never on a page count (C11 fix round): the wrapper republishes iiif.json
  // every ten pages, but a count crossing zero does not mean that publish has
  // actually happened yet, and a volume smaller than that cadence would
  // otherwise link to a manifest that is not there.
  function openHref(v: VolumeView): string | null {
    const published =
      v.state === "done" || (v.progress?.viewerPublished ?? false);
    const manifest = published ? v.iiifUrl : sourceOf(v);
    // Checked and encoded like every other URL this card turns into an
    // href: `iiifUrl` is built by the API from a volume id that came off a
    // campaign's volumes.txt, and it went into the fragment unread and
    // unescaped (2026-09-14 audit).
    if (manifest === null || !isHttpUrl(manifest)) return null;
    return `uv.html#?manifest=${encodeURIComponent(manifest)}`;
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
      (v.state !== "done" && v.state !== "unknown" ? "&live=1" : "")
    );
  }

  // The notice's own link: the log of the volume the error came from, which
  // is usually not one of the rows loaded here, so it is built from the URL
  // the API sent rather than from a row. Live, because a campaign showing a
  // notice is nearly always still running.
  const noticeHref = $derived(
    notice.lastError === null || !isHttpUrl(notice.lastError.logUrl)
      ? null
      : `log?log=${encodeURIComponent(notice.lastError.logUrl)}&live=1`,
  );
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

<!-- One track, three callers (a running volume's row and the campaign's two
     fractions), so no two bars can disagree about what a fraction looks
     like. `aria-label` names the thing being measured in full, since the bar
     has no text and the figures beside it are a separate element. `mode` is
     the one thing that varies: the sheen crosses a bar only while work is
     actually happening, and the fill goes amber when the campaign finished
     with pages missing. -->
{#snippet bar(label: string, done: number, total: number, mode: string)}
  <span
    class="bar"
    role="progressbar"
    aria-label={label}
    aria-valuenow={clamp(done, total)}
    aria-valuemin={0}
    aria-valuemax={Math.max(total, 0)}
  >
    <span class="fill {mode}" style="width: {pct(done, total)}"></span>
  </span>
{/snippet}

<!-- One cell of the numbers line: label, bar, figures. The three tracks are
     fixed lengths, not content-derived, so the figures of ten stacked cards
     sit on one vertical line and the eye can run down them. -->
{#snippet metric(
  label: string,
  cell: { done: number; total: number; failed: number; active?: number },
  mode: string,
)}
  <span class="metric">
    <span class="metric-label">{label}</span>
    <span class="metric-bar">
      {#if cell.total > 0}
        {@render bar(
          `${label} done in campaign ${job.name}`,
          cell.done,
          cell.total,
          mode,
        )}
      {/if}
    </span>
    <!-- The separators carry their own leading space: Svelte trims the
         whitespace in front of an element, and the live page read
         "5 / 8· 3 failed" (2026-09-16 review). -->
    <span class="metric-figures"
      >{figures(cell)}{#if cell.failed > 0}<span class="bad"
          >{" · "}{cell.failed} failed</span
        >{/if}{#if cell.active}<span>{" · "}{cell.active} active</span
        >{/if}</span
    >
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
    {#if warmupChip !== null}
      <span
        class="chip warmup {job.warmup.phase}"
        title={job.warmup.reason
          ? describeReason(job.warmup.reason)
          : undefined}>{warmupChip}</span
      >
    {/if}
    <span
      class="chip phase {job.phase.toLowerCase()}"
      class:lost={campaignLost}
      title={campaignLost ? doneWith(notice.pagesFailed) : undefined}
    >
      {#if beating}<span class="dot pulse" aria-hidden="true"
        ></span>{/if}{#if campaignLost}<span aria-hidden="true"
          >{phaseLabel}</span
        ><span class="sr-only">{doneWith(notice.pagesFailed)}</span
        >{:else}{phaseLabel}{/if}</span
    >
    {#if job.jobGone}
      <!-- Not a failure: the Job did its work and Kubernetes removed it at
           its TTL. The chip says why there is no volume table below. -->
      <span
        class="chip gone"
        title="the campaign's Job has passed its ttlSecondsAfterFinished and been removed — this is the record it left"
        >job removed</span
      >
    {/if}
    <!-- When the campaign was created and when it finished, at the right end
         of the identity line. -->
    {#if job.createdAt !== null || job.finishedAt !== null}
      <span class="when">
        {#if job.createdAt !== null}
          <span class="sr-only">created </span><time
            datetime={job.createdAt}
            title={job.createdAt}
            >{shortDate(job.createdAt) ?? job.createdAt}</time
          >
        {/if}
        {#if finishedLabel !== null}
          <span class="arrow" aria-hidden="true">→</span><span class="sr-only"
            >, finished
          </span><time datetime={job.finishedAt} title={job.finishedAt}
            >{finishedLabel}</time
          >
        {:else if stillGoing}
          <span class="arrow" aria-hidden="true">→</span><span
            aria-hidden="true">…</span
          ><span class="sr-only">, still running</span>
        {/if}
      </span>
    {/if}
  </div>

  <!-- Zone 2. Every card carries these three cells, in the same columns,
       whether or not the numbers in them are known yet. -->
  <div class="numbers">
    {@render metric(
      "volumes",
      volumeCell,
      job.phase === "Running" ? "running" : campaignLost ? "lost" : "",
    )}
    {@render metric(
      "pages",
      pageCell,
      job.phase === "Running" ? "running" : campaignLost ? "lost" : "",
    )}
    {#if notice.errors > 0}
      <span class="metric errors">
        <span class="metric-label">errors</span>
        <span class="metric-figures bad">{notice.errors}</span>
      </span>
    {/if}
  </div>

  <!-- Zone 3. Only when something is wrong, and never the numbers above. -->
  {#if problems.length > 0}
    <!-- The sentences are real text, not a hidden copy of themselves:
         clipping with `overflow` leaves them in the accessibility tree, and
         a second copy beside the links below would be read twice
         (2026-09-16 review). The `title` is for the mouse. -->
    <p class="problems" title={problemsText}>
      <span class="problems-text"
        >{#each problems as part, i (i)}{i > 0
            ? " · "
            : ""}{#if part.id !== null}{#if part.href === null}<span class="pid"
                >{part.id}</span
              >{:else}<a class="pid" href={part.href}>{part.id}</a
              >{/if}{": "}{/if}{part.text}{/each}</span
      >
      {#if noticeHref !== null}
        <a class="problems-log" href={noticeHref}>log</a>
      {/if}
    </p>
  {/if}
  <!-- Zone 4. Which weights produced these results: provenance, and the
       least often read line on the card, so it sits last and lightest. The
       dates it used to share a line with are at the right end of zone 1 now
       (the product owner, 2026-09-16). No expander behind a count: a real
       pipeline names two or three models
       (examples/campaigns/pipelines/demo-v1.yaml), so the whole line fits a
       normal card and clips, with its own title, on a narrow one. -->
  {#if models.length > 0}
    <p class="card-meta">
      <span class="models" title="Models: {models.map(modelLabel).join(' · ')}">
        Models:
        {#each models as model, i (i)}
          {@const href = modelUrl(model)}
          {i > 0 ? " · " : ""}
          {#if href === null}
            {modelLabel(model)}
          {:else}
            <a {href} target="_blank" rel="noopener">{modelLabel(model)}</a>
          {/if}
        {/each}
      </span>
    </p>
  {/if}
  {#if yamlOpen && pipelineYaml !== ""}
    <pre class="pipeline-yaml" id={yamlId}>{pipelineYaml}</pre>
  {/if}
  {#if detailError !== null}
    <p class="notice error-row" role="alert">{detailError}</p>
  {/if}
  {#if collapsed && latest !== null}
    {@const lost = lostPages(latest)}
    <p class="latest">
      <span
        class="latest-state {latest.state}"
        class:lost={lost > 0}
        title={lost > 0 ? doneWith(lost) : undefined}
        >{#if lost > 0}<span aria-hidden="true">{latest.state}</span><span
            class="sr-only">{doneWith(lost)}</span
          >{:else}{latest.state}{/if}</span
      >
      <span class="latest-id" title={latest.id}>{latest.id}</span>
      <span class="links">{@render links(latest)}</span>
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
            {@const lost = lostPages(v)}
            <tr>
              <td class="vid">
                <span class="vid-name" title={v.id}>{v.id}</span>
                {#if v.reason !== undefined}
                  <span class="verr">{describeReason(v.reason)}</span>
                {/if}
              </td>
              <td>
                <span
                  class="status {v.state}"
                  class:lost={lost > 0}
                  title={lost > 0 ? doneWith(lost) : undefined}
                >
                  <span
                    class="dot"
                    class:pulse={v.state === "active"}
                    aria-hidden="true"
                  ></span>
                  {#if lost > 0}<span aria-hidden="true">{v.state}</span><span
                      class="sr-only">{doneWith(lost)}</span
                    >{:else}{v.state}{/if}
                </span>
                {#if v.progress !== null}
                  <!-- Keyed on the count itself: the node is rebuilt only
                       when the number changes, which is what restarts the
                       highlight; `moved` is what decides it runs at all. -->
                  {#key v.progress.done}
                    <span class="vprogress" class:bump={moved.has(v.id)}
                      >{describeProgress(v.progress)}</span
                    >
                  {/key}
                  {#if v.state === "active" && v.progress.total > 0}
                    {@render bar(
                      `Pages done in ${v.id}`,
                      v.progress.done,
                      v.progress.total,
                      "running",
                    )}
                  {/if}
                {/if}
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

  /* Published, but not all of it. The same warning token the phase chip
     gives a partially failed campaign. */
  .campaign[data-health="lost"] {
    border-left-color: var(--warning);
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

  /* Pushed to the right end of the identity line, and the first thing to
     wrap under it when the chips take the width (a phone). */
  .when {
    margin-left: auto;
    color: var(--muted-foreground);
    font-size: 0.75rem;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }

  .when .arrow {
    padding: 0 0.15em;
    opacity: 0.7;
  }

  /* Zone 2. Fixed tracks, not content-derived: ten stacked cards put their
     figures on one vertical line, which is what makes the list scan like a
     table rather than like ten paragraphs. */
  .numbers {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 6.5rem;
    align-items: center;
    gap: 0.2rem 1rem;
    margin: 0.35rem 0 0;
    font-size: 0.8rem;
    color: var(--muted-foreground);
    font-variant-numeric: tabular-nums;
  }

  .metric {
    display: grid;
    grid-template-columns: 3.6rem 4.5rem minmax(0, 1fr);
    align-items: center;
    gap: 0 0.5rem;
    min-width: 0;
  }

  .metric.errors {
    grid-template-columns: 3.6rem minmax(0, 1fr);
  }

  .metric-label {
    color: var(--muted-foreground);
  }

  /* The track keeps its width even with no bar in it, so a campaign whose
     totals are not known yet does not shuffle the figures of the card
     above it. */
  .metric-bar {
    display: block;
  }

  .metric-figures {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--foreground);
  }

  /* Zone 3. Warning, not error: a campaign saying this has usually
     published most of itself. One line, clipped, with the whole of it in
     the title and in a visually-hidden copy beside it. */
  .problems {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
    margin: 0.35rem 0 0;
    font-size: 0.78rem;
    color: var(--warning);
  }

  .problems-text {
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* The volume's id is the link, so the sentence beside it stays a
     sentence and the line stays one line. */
  .pid {
    font-weight: 500;
    color: inherit;
  }

  a.pid {
    text-decoration: underline;
    text-underline-offset: 0.15em;
  }

  a.pid:hover {
    color: var(--primary);
  }

  a.pid:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
    border-radius: 3px;
  }

  .problems-log {
    flex-shrink: 0;
    color: inherit;
    text-decoration: underline;
    text-underline-offset: 0.15em;
  }

  .problems-log:hover {
    color: var(--primary);
  }

  .problems-log:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
    border-radius: 3px;
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

  /* Neutral on purpose: the Job's removal is housekeeping, not a verdict on
     the campaign -- the phase chip beside it already carries that. */
  .chip.gone {
    border: 1px solid var(--border);
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

  /* Zone 4, and the quietest line on the card: provenance is what a reader
     checks once, not what they came for. A step lighter than the numbers
     above it, in the same muted colour. The models clip rather than wrap,
     with their title carrying the list the clip cut -- `clip` with a margin
     rather than `hidden`, so a focus ring on the last link is not shaved
     off. */
  .card-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.15rem 0.75rem;
    margin: 0.35rem 0 0;
    font-size: 11.5px;
    font-weight: 400;
    color: var(--muted-foreground);
    opacity: 0.9;
  }

  .models {
    min-width: 0;
    overflow: clip;
    overflow-clip-margin: 4px;
    white-space: nowrap;
    text-overflow: ellipsis;
  }

  /* Links in the line's own colour, and the underline in that colour too:
     it carries the contrast the text does, so it is what marks a link at
     rest rather than a hairline nobody can see. Hover and focus spend the
     accent, and focus takes the same ring every other control here wears. */
  .models a {
    color: inherit;
    text-decoration: underline;
    text-decoration-color: currentColor;
    text-underline-offset: 0.15em;
  }

  .models a:hover {
    color: var(--primary);
  }

  .models a:focus-visible {
    color: var(--primary);
    outline: 2px solid var(--primary);
    outline-offset: 2px;
    border-radius: 3px;
  }

  .chip.phase.succeeded {
    background: var(--success-soft);
    color: var(--success);
  }

  .chip.phase.running {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    background: var(--primary-soft);
    color: var(--primary);
  }

  .chip.phase.queued,
  .chip.phase.paused,
  /* Not a failure and not a success: nobody wrote down how it ended. */
  .chip.phase.unknown,
  .chip.warmup.pending,
  .chip.warmup.running,
  /* Warning, not error: some of the campaign did publish — whether the Job
     said so, or every index succeeded and pages were lost inside them. */
  .chip.phase.lost,
  .chip.phase.partiallyfailed {
    background: var(--warning-soft);
    color: var(--warning);
  }

  .chip.phase.failed,
  .chip.warmup.failed,
  .chip.warmup.missing {
    background: var(--destructive);
    color: var(--on-strong);
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

  /* Wide enough for "137 / 638 pages · processing pages · updated 12 s ago"
     to wrap to two lines rather than five. */
  col.c-status {
    width: 13rem;
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
      width: 7rem;
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

  /* Under the state chip, not beside it: the state is what the eye scans
     down the column, the numbers are what it stops for. */
  .vprogress {
    display: block;
    font-size: 11.5px;
    color: var(--muted-foreground);
    overflow-wrap: anywhere;
  }

  /* The one thing on this card that moves of its own accord. The fill eases
     to its new width when a poll lands, so a reader watching sees it fill
     rather than find it moved; the sheen crossing it says the volume is
     still working through the minute in between. A done volume gets no bar
     at all -- a bar that cannot move is just a green line, and it would
     dilute the only signal this is here to carry. */
  .bar {
    display: block;
    height: 3px;
    margin-top: 0.2rem;
    border-radius: 999px;
    background: var(--muted);
    overflow: hidden;
  }

  .fill {
    display: block;
    position: relative;
    height: 100%;
    border-radius: inherit;
    overflow: hidden;
    background: var(--primary);
    transition: width 600ms ease-out;
  }

  /* Published, but not all of it: the same warning token the phase chip
     takes in the same state. */
  .fill.lost {
    background: var(--warning);
  }

  /* --background, not white: it reads as a light band on the light theme's
     dark blue and a dark one on the dark theme's pale blue, from one rule.
     Only on a bar whose work is actually happening -- every card carries
     bars now, and a sheen crossing a finished one would say the opposite of
     what it means. */
  .fill.running::after {
    content: "";
    position: absolute;
    inset: 0;
    opacity: 0.45;
    background: linear-gradient(
      90deg,
      transparent,
      var(--background),
      transparent
    );
    animation: sheen 2.5s linear infinite;
  }

  @keyframes sheen {
    from {
      transform: translateX(-100%);
    }
    to {
      transform: translateX(100%);
    }
  }

  /* Third gesture: one second of the running blue behind the line whose page
     count just moved, so a poll landing is something a reader catches rather
     than infers. The colour is the line's own background for the length of
     the animation and nothing before or after it -- no pseudo-element, so
     there is no paint order to get wrong. It fades to a zero-alpha
     `--primary-soft` rather than to `transparent`, which is black at alpha 0
     and would grey on the way out. */
  .vprogress.bump {
    border-radius: 3px;
    animation: settle 1s ease-out;
  }

  @keyframes settle {
    from {
      background-color: var(--primary-soft);
    }

    to {
      background-color: color-mix(
        in oklab,
        var(--primary-soft) 0%,
        transparent
      );
    }
  }

  .status {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    white-space: nowrap;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
  }

  /* Shared by the row's state chip and the header's phase chip, so the two
     dots cannot drift apart. */
  .dot {
    width: 0.5em;
    height: 0.5em;
    border-radius: 50%;
    background: currentColor;
    flex-shrink: 0;
  }

  /* A slow heartbeat, and only on what is actually running -- never on a
     done, failed, queued or paused thing. A page where everything twitches
     tells a reader nothing; one dot beating tells them where to look. */
  .dot.pulse {
    animation: beat 2s ease-in-out infinite;
  }

  /* One keyframe: the ends are the element's own opacity and scale. */
  @keyframes beat {
    50% {
      opacity: 0.3;
      transform: scale(0.7);
    }
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

  /* Done, and pages went missing doing it: the green said the volume was
     clean. Wins over .status.done above, which it follows. */
  .status.done.lost {
    color: var(--warning);
    background: var(--warning-soft);
  }

  /* Nobody recorded what this volume did: the same quiet treatment as a
     volume that has not started, since neither is a failure. */
  .status.unknown,
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

  .latest-state.done.lost {
    color: var(--warning);
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

  /* A phone. Zone 2's three cells take a line each -- the columns still
     line up, they are just one per row -- and zone 1's dates drop under the
     chips rather than squeezing the campaign's name. */
  @media (max-width: 520px) {
    .numbers {
      grid-template-columns: minmax(0, 1fr);
    }

    .when {
      margin-left: 0;
      flex-basis: 100%;
    }
  }

  /* app.css already shortens every animation to nothing; this says it
     outright, so the dot cannot be left parked mid-beat. */
  @media (prefers-reduced-motion: reduce) {
    .dot.pulse {
      animation: none;
    }

    /* The highlight lives entirely inside its keyframes, so turning the
       animation off leaves no colour behind. The sheen is a real element and
       has to be hidden, not merely stilled, or it parks across the fill. */
    .vprogress.bump {
      animation: none;
    }

    .fill.running::after {
      display: none;
    }

    .fill {
      transition: none;
    }
  }
</style>
