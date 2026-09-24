<script lang="ts">
  // One campaign = one Indexed Job. The header is the JobSummary the parent
  // already has (from GET /api/v1/jobs); the volume table is fetched
  // separately and paged (GET /api/v1/jobs/{ns}/{name}?offset&limit), since
  // a campaign can carry thousands of volumes. No thumbnails: the read API
  // has no per-volume image, only the finished results.
  import {
    clockTime,
    fetchJob,
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

  // `showNamespace`: the list spans namespaces, so the name alone may not
  // tell two campaigns apart. The list decides it, once, for every card.
  let {
    job,
    showNamespace = false,
  }: { job: JobSummary; showNamespace?: boolean } = $props();

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

  const startsFolded = remembered();
  let collapsed = $state(startsFolded);

  // Whether anyone has had a chance to see this card: it has been on screen,
  // or open. A finished campaign reads its detail only then -- a page of
  // them used to make one request per card the moment it opened, each
  // reading the campaign's volume list, its pods and up to a hundred
  // progress files, for cards nobody had scrolled to (the 2026-09-23
  // audit). Sticky: once seen, folding the card is not a reason to forget.
  let wanted = $state(!startsFolded);

  function toggle(): void {
    collapsed = !collapsed;
    if (!collapsed) wanted = true;
    try {
      localStorage.setItem(memoryKey, collapsed ? "closed" : "open");
    } catch {
      // Nothing to remember it with; the card still opens and closes.
    }
  }
  let yamlOpen = $state(false); // collapsed by default
  let volumes = $state<VolumeView[]>([]);
  let failures = $state<VolumeView[]>([]);
  // Failures the reader cannot already see: a failed row on the loaded page
  // is right there with its reason, so the callout must not say it again
  // (the product owner, 2026-09-08: "we don't need to list a failure
  // twice").
  let unseenFailures = $derived(
    failures.filter((f) => !volumes.some((v) => v.id === f.id)),
  );
  // The pages of the volumes this response covered, summed by the API. Not
  // in JobSummary: the list endpoint reads no volumes at all.
  let pages = $state<{ done: number; total: number }>({ done: 0, total: 0 });
  // How many run volumes those pages are summed over, of how many (3076).
  let coverage = $state({ counted: 0, of: 0 });
  // What went wrong anywhere in the campaign: failed pages, errors, and the
  // most recent error with the run log it came from (the product owner,
  // 2026-09-08 — an exception in a log should show on the front page).
  let notice = $state<CampaignNotice>({
    pagesFailed: 0,
    errors: 0,
    lastError: null,
  });
  // Zone 3. Only when something is wrong. It merges what used
  // to be two things: a chip in the header row that repeated counts the
  // numbers line already shows, and a bulleted callout below it that said
  // the same failures again (the product owner, 2026-09-16). What is left
  // is the sentences and nothing else -- why the warm-up could not run, why
  // each volume failed, and the last page error -- in the order a reader
  // needs them.
  const lastErrorText = $derived(describeLastError(notice.lastError));
  // A page error is one volume's -- it is that volume's own
  // `progress.lastError` -- so it is said under that volume's row rather
  // than in a line about the campaign (the product owner, 2026-09-16: "seems
  // misplaced"). Only when the volume is on screen to say it under; a
  // campaign whose failing volume is on a page nobody has loaded keeps the
  // sentence in the campaign's line, where it is at least not lost.
  const lastErrorVolume = $derived(
    notice.lastError !== null &&
      volumes.some((v) => v.id === notice.lastError?.volume)
      ? notice.lastError.volume
      : null,
  );
  // A warm-up reason is only ever shown once the warm-up Job has failed,
  // which is after its own backoffLimit is spent: nothing retries it.
  const warmupReason = $derived(
    job.warmup.reason ? describeReason(job.warmup.reason, true) : undefined,
  );
  const problems = $derived([
    ...(warmupReason !== undefined
      ? [{ id: "warm-up", text: warmupReason, href: null }]
      : []),
    // Each failed volume keeps its own run log, the way the callout this
    // line replaced did: a failure a reader can read about but not open is
    // half a message (2026-09-16 review). The id is the link.
    ...unseenFailures.map((f) => ({
      id: f.id,
      text: reasonOf(f),
      href: logHref(f),
    })),
    ...(lastErrorText === null || lastErrorVolume !== null
      ? []
      : [{ id: null, text: lastErrorText, href: null }]),
  ]);
  // The line wraps rather than clips -- clipped, the second failure on was
  // unreadable on a phone or from a keyboard, and a Tab could land on a link
  // nobody could see (the 2026-09-17 audit, 3080). A campaign can carry up
  // to 50, so past PROBLEMS_SHOWN the rest wait behind a button instead of
  // burying the volumes under a paragraph; the ones held back are not
  // rendered at all, so there is no hidden link to focus.
  const PROBLEMS_SHOWN = 3;
  let allProblems = $state(false);
  const shownProblems = $derived(
    allProblems ? problems : problems.slice(0, PROBLEMS_SHOWN),
  );
  const heldBack = $derived(problems.length - PROBLEMS_SHOWN);
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
    // Said while the sums are not yet every volume's: a clean-looking total
    // may only be one that has not read the volume that lost pages.
    counting: coverage.counted < coverage.of ? coverage : undefined,
  });

  /** A volume's pages in the shape zone 2 uses for the campaign's. */
  function volumePages(v: VolumeView) {
    return {
      done: v.progress?.done ?? 0,
      total: v.progress?.total ?? 0,
      failed: v.progress?.failed ?? 0,
    };
  }

  /**
   * A volume's bar, in that volume's own colour: blue and sheening while it
   * works, green when it finished clean, amber when it finished without some
   * of its pages, red when it failed. `pending` and `unknown` get none at
   * all — a bar of nothing over nothing is worse than an empty track (the
   * product owner, 2026-09-16, on a single-volume card having no bar
   * anywhere: the volume rows carried none).
   */
  function volumeMode(v: VolumeView): string {
    if (v.state === "active") return "running";
    if (v.state === "failed") return "failed";
    return lostPages(v) > 0 ? "lost" : "done";
  }

  function hasBar(v: VolumeView): boolean {
    return (
      (v.progress?.total ?? 0) > 0 &&
      v.state !== "pending" &&
      v.state !== "unknown"
    );
  }

  /**
   * Whether anything will run this volume again. `failed` is the Job's
   * failedIndexes -- the index has spent its backoffLimitPerIndex -- so a
   * transient cause is no promise of a retry there; an `active` volume
   * carrying a reason is one between attempts (the 2026-09-17 audit, 3078).
   */
  function final(v: VolumeView): boolean {
    return v.state === "failed";
  }

  /** What this volume has to say for itself under its own row. */
  function noteText(v: VolumeView): string {
    const parts = [];
    if (v.reason !== undefined) parts.push(describeReason(v.reason, final(v)));
    if (lastErrorVolume === v.id && lastErrorText !== null)
      parts.push(lastErrorText);
    return parts.join(" · ");
  }

  /** Why a volume failed, in one sentence. */
  function reasonOf(v: VolumeView): string {
    return v.reason === undefined
      ? "Failed, with no message from the pod."
      : describeReason(v.reason, final(v));
  }

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
  const openId = $derived(`campaign-${slug}`);
  const problemsId = $derived(`problems-${slug}`);
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

  // The bar's mood, shared by both totals rows: the sheen crosses them only
  // while work is happening, and they go amber with the chip when the
  // campaign finished with pages missing.
  const rowMode = $derived(
    job.phase === "Running" ? "running" : campaignLost ? "lost" : "",
  );

  // A campaign of one volume has nothing to sum: its own row carries the
  // same two fractions, and stacking a total over an identical row said
  // everything twice (the product owner, 2026-09-16). Only dropped once
  // there IS a row to carry them -- a detail that has not loaded yet would
  // otherwise leave the card with no numbers at all.
  const showTotals = $derived(job.counts.total !== 1 || volumes.length === 0);

  // The two "partially" words shared one amber, on the chip and on the
  // accent, so a campaign that lost a few pages and one that lost whole
  // volumes looked the same at a glance (a maintainer request). Each now
  // mixes the amber with where it ended: green when every volume finished,
  // red when volumes were lost. The words and the tooltip still say it;
  // the colour is the second cue, never the only one.
  const mix = $derived(
    job.phase === "PartiallyFailed"
      ? "destructive"
      : campaignLost
        ? "success"
        : undefined,
  );

  // The card's left accent: worst-first, read straight off the Job phase now
  // that the API computes it server-side. `inTrouble` is $lib/order's --
  // the same question the campaign list sorts by, so the accent and the
  // order can never disagree about what is wrong (2026-09-16 review). A
  // partially failed campaign is in trouble too; it only says so in its
  // own mix.
  const health = $derived(
    job.phase === "PartiallyFailed"
      ? "partly-failed"
      : inTrouble(job)
        ? "failed"
        : job.phase === "Running"
          ? "active"
          : campaignLost
            ? "partly-succeeded"
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

  // The one place the chip's word is decided. A campaign whose Job succeeded
  // but whose volumes lost pages used to wear "Succeeded" painted amber --
  // the word and the colour saying different things (the product owner,
  // 2026-09-16: "we have partially failed, maybe we should have partially
  // succeeded also"). The two "partially" words are a pair and mean
  // different losses: `PartiallyFailed` is whole VOLUMES that never
  // published, "partially succeeded" is every volume finishing without some
  // of its PAGES.
  const phaseLabel = $derived(
    job.phase === "PartiallyFailed"
      ? "partially failed"
      : job.phase === "Unknown"
        ? "outcome unknown"
        : campaignLost
          ? "partially succeeded"
          : job.phase,
  );

  /** "every volume finished, 3 pages failed" — the chip's own tooltip. */
  const phaseTitle = $derived(
    campaignLost
      ? `every volume finished, ${notice.pagesFailed} ` +
          `page${notice.pagesFailed === 1 ? "" : "s"} failed`
      : undefined,
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
      pages = { done: detail.pagesDone, total: detail.pagesTotal };
      coverage = detail.pagesCoverage;
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

  // A campaign that has finished -- or whose Job is gone -- cannot change,
  // so its card reads the detail once rather than every minute for as long
  // as the page is open: each call lists pods, reads the campaign's
  // ConfigMaps and up to a hundred progress files (the API's
  // PROGRESS_FETCH_CAP), and a page of old campaigns polling for ever was
  // load that grew with the history and bought nothing (the 2026-09-17
  // audit, 3079). "Once" means once it has landed: a read that failed is
  // still retried, on $lib/poll's backoff. And only once the card is
  // `wanted` -- on screen or open.
  const settled = $derived(
    job.jobGone ||
      job.phase === "Succeeded" ||
      job.phase === "Failed" ||
      job.phase === "PartiallyFailed" ||
      job.phase === "Unknown",
  );

  // The campaign state the last read that landed was of: a settled card is
  // not read again for the same state, however often it is folded and
  // opened. Not reactive: it is the effect's memory, not its input.
  let landedFor = "";

  $effect(() => {
    // Tracked on purpose: a change of phase, or the Job being reaped, is
    // news -- a campaign that finishes while shown is read in its final
    // state, a reaped one from the record that replaced its Job, and one
    // re-run under the same name starts polling again.
    const state = `${job.phase}/${job.jobGone}`;
    // Folded, the card is its header, and the list row carries all of that
    // but one thing: whether a Succeeded campaign lost pages on the way
    // ("partially succeeded"). Nothing else is read for a folded card.
    if (collapsed && job.phase !== "Succeeded") return;
    const once = settled || collapsed;
    // Read only for a settled card, so a running one is not restarted by it.
    if (once && (!wanted || landedFor === state)) return;
    // untrack: load() reads `volumes` to size its refresh and then writes it,
    // and an effect that reads its own output re-runs forever. The list
    // keys each card by namespace/name, so a card never changes campaign
    // under its own feet. $lib/poll is what keeps a page of cards from each
    // queueing up requests against a slow API, and from polling at all
    // while nobody is looking.
    return untrack(() =>
      startPolling(
        async (signal) => {
          const landed = await load(true, signal);
          if (landed && !signal.aborted) landedFor = state;
          return landed;
        },
        RELOAD_MS,
        { until: () => once && landedFor === state },
      ),
    );
  });

  /**
   * Marks the card `wanted` the first time any of it is on screen (or
   * nearly: the margin reads it just before it scrolls in). A browser with
   * no IntersectionObserver cannot say, so the card is wanted at once --
   * what every card did before.
   */
  function whenSeen(node: HTMLElement) {
    if (typeof IntersectionObserver === "undefined") {
      wanted = true;
      return {};
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((e) => e.isIntersecting)) return;
        wanted = true;
        observer.disconnect();
      },
      { rootMargin: "200px" },
    );
    observer.observe(node);
    return { destroy: () => observer.disconnect() };
  }

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
    const manifest = published ? v.iiifUrl : v.sourceUrl;
    // Every URL this card turns into an href has been through $lib/api's
    // httpUrlSchema, the one gate: a detail whose API-built URL is not an
    // absolute http(s) one is refused whole, and a `sourceUrl` -- a line of
    // a volumes.txt people edit -- that is not one arrives as null. Encoded,
    // since `iiifUrl` is built from a volume id and went into the fragment
    // unescaped once (2026-09-14 audit).
    if (manifest === null) return null;
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
    notice.lastError === null
      ? null
      : `log?log=${encodeURIComponent(notice.lastError.logUrl)}&live=1`,
  );
</script>

<!-- "Maybe we can have the volume name as a link to open the viewer, then
     have a symbol for log and manifest?" (the product owner, 2026-09-16).
     Three words of link text per row read as three words; the id a reader is
     already looking at is the thing they want to open, and the two documents
     behind it are recognisable as glyphs. Both icons are inline SVG: the
     page's CSP allows no fetched asset and no third-party script, and a
     glyph font would be both. -->
{#snippet glyph(kind: string)}
  {#if kind === "log"}
    <!-- A page with lines on it: the run log. -->
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      stroke-width="1.3"
      stroke-linecap="round"
      stroke-linejoin="round"
    >
      <path d="M4 2h5l3 3v9H4z" />
      <path d="M9 2v3h3M6 8.5h4M6 11h4" />
    </svg>
  {:else}
    <!-- Braces: a document of data, not of words. -->
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      stroke-width="1.3"
      stroke-linecap="round"
      stroke-linejoin="round"
    >
      <path
        d="M6.5 2.5h-1A1.5 1.5 0 0 0 4 4v2.5L2.5 8 4 9.5V12a1.5 1.5 0 0 0 1.5 1.5h1"
      />
      <path
        d="M9.5 2.5h1A1.5 1.5 0 0 1 12 4v2.5L13.5 8 12 9.5V12a1.5 1.5 0 0 1-1.5 1.5h-1"
      />
    </svg>
  {/if}
{/snippet}

<!-- The id, linked to the viewer when there is something to open there.
     Never a link that goes nowhere: a volume with no published result and no
     source manifest is plain text saying why. "Yet" is a promise, so it is
     kept for the volumes that may still keep it -- one that failed, or whose
     outcome nobody recorded, is not going to publish a manifest (2026-09-16
     review). -->
{#snippet volumeId(v: VolumeView)}
  {@const open = openHref(v)}
  {@const coming = v.state === "pending" || v.state === "active"}
  {#if open === null}
    <span
      class="vid-name"
      title="{v.id} — {coming
        ? 'nothing to open there yet'
        : 'no viewer manifest for this volume'}">{v.id}</span
    >
  {:else}
    <a
      class="vid-name"
      href={open}
      target="_blank"
      rel="noopener"
      title="open {v.id} in the viewer">{v.id}</a
    >
  {/if}
{/snippet}

<!-- Two fixed slots, source manifest then run log (where it came from, then
     what happened), so a volume with no source leaves a gap, not a shift. -->
{#snippet links(v: VolumeView)}
  {@const source = v.sourceUrl}
  <span class="slot"
    >{#if source !== null}<a
        class="vicon"
        href={source}
        target="_blank"
        rel="noopener"
        aria-label="manifest for {v.id}"
        title="manifest for {v.id}">{@render glyph("manifest")}</a
      >{/if}</span
  >
  <span class="slot"
    ><a
      class="vicon"
      href={logHref(v)}
      aria-label="run log for {v.id}"
      title="run log for {v.id}">{@render glyph("log")}</a
    ></span
  >
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

<!-- What a row lost, under its own bar and nowhere else: "1 failed", or
     "3 failed · 2 errors" on the pages total. A line of its own in the bar's
     column, so a clean row is one line and nothing moves when there is
     nothing wrong (the product owner, 2026-09-16: "if we have any failed,
     put it UNDER the loading bar for that row"). `errors` rides here rather
     than taking a column -- a count with no fraction beside it lined up with
     nothing. -->
{#snippet lostLine(failed: number, errors: number)}
  {#if failed > 0 || errors > 0}
    <span class="c-lost"
      >{#if failed > 0}{`${failed} failed`}{/if}{#if failed > 0 && errors > 0}{" · "}{/if}{#if errors > 0}{`${errors} error${
          errors === 1 ? "" : "s"
        }`}{/if}</span
    >
  {/if}
{/snippet}

<!-- A campaign total: the same five tracks a volume row has, with the icons
     and the pill left empty, so the fraction the card sums sits in the same
     column as the fraction of every volume under it -- and so does its
     bar. -->
{#snippet totalsRow(
  label: string,
  cell: {
    done: number;
    total: number;
    failed: number;
    active?: number;
    counting?: { counted: number; of: number } | undefined;
  },
  errors: number,
)}
  <div class="row totals">
    <span class="c-label"
      >{label}{#if cell.active}<span class="quiet">
          {" · "}{cell.active} active</span
        >{/if}{#if cell.counting}<span class="quiet">
          {" · "}counted in {cell.counting.counted} of {cell.counting.of} volumes</span
        >{/if}</span
    >
    <span class="c-links"></span>
    <span class="c-bar"
      >{#if cell.total > 0}{@render bar(
          `${label} done in campaign ${job.name}`,
          cell.done,
          cell.total,
          rowMode,
        )}{/if}</span
    >
    <span class="c-fraction">{figures(cell)}</span>
    <span class="c-status"></span>
    {@render lostLine(cell.failed, errors)}
  </div>
{/snippet}

<!-- The one sentence a volume has to say for itself, under its own row and
     across the whole width: why it failed, or the page error it reported.
     A fifth cell spanning the row rather than a line inside the id\'s cell,
     which is capped at 16rem and would clip a sentence to nothing. -->
{#snippet volumeNote(v: VolumeView, cellRole: string | undefined)}
  {#if v.reason !== undefined || lastErrorVolume === v.id}
    <p class="row-note" role={cellRole}>
      <span class="row-note-text">{noteText(v)}</span>
      {#if lastErrorVolume === v.id && noticeHref !== null}
        <a class="problems-log" href={noticeHref}>log</a>
      {/if}
    </p>
  {/if}
{/snippet}

<!-- A volume, as the same four tracks the totals above it use: the id (and,
     when the row has room, what went wrong and what it is doing), the bar,
     the figures, and the actions. With the tracks shared, every number on
     the card sits in one column and every pill and icon in another (the
     product owner, 2026-09-16: "the layout of the columns is a bit bad").
     `cellRole` is the ARIA table's cell role. -->
{#snippet volumeRow(v: VolumeView, cellRole: string | undefined)}
  {@const lost = lostPages(v)}
  {@const cell = volumePages(v)}
  {@const story =
    v.progress === null ? "" : describeProgress(v.progress, v.state)}
  <span class="c-label" role={cellRole}>
    <span class="vid-line">{@render volumeId(v)}</span>
    {#if story !== ""}<span class="vprogress">{story}</span>{/if}
  </span>
  <span class="c-links" role={cellRole}>{@render links(v)}</span>
  <span class="c-bar" role={cellRole}
    >{#if hasBar(v)}{@render bar(
        `Pages done in ${v.id}`,
        cell.done,
        cell.total,
        volumeMode(v),
      )}{/if}</span
  >
  <!-- Keyed on the count itself: the node is rebuilt only when the number
       changes, which is what restarts the highlight; `moved` is what decides
       it runs at all. -->
  <span class="c-fraction" role={cellRole}>
    {#key cell.done}
      <span class="vfigures" class:bump={moved.has(v.id)}>{figures(cell)}</span>
    {/key}
  </span>
  <!-- The pill is the fixed-width element, so it is the one that can anchor
       the right edge of every row (2026-09-16). -->
  <span class="c-status" role={cellRole}>
    <span
      class="status {v.state}"
      class:lost={lost > 0}
      title={lost > 0 ? doneWith(lost) : undefined}
    >
      <span class="dot" class:pulse={v.state === "active"} aria-hidden="true"
      ></span>
      <span class="status-word"
        >{#if lost > 0}<span aria-hidden="true">{v.state}</span><span
            class="sr-only">{doneWith(lost)}</span
          >{:else}{v.state}{/if}</span
      >
    </span>
  </span>
  {@render lostLine(cell.failed, 0)}
{/snippet}

<section class="campaign" data-health={health} use:whenSeen>
  <div class="camp">
    <!-- aria-controls only while the card is open: it must be an IDREF that
         resolves, and a folded card renders nothing below this line.
         aria-expanded alone carries the open/closed state, and aria-controls
         is optional in the disclosure pattern; rendering an empty element
         just to keep the id would be worse — the reference would resolve to
         nothing at all. -->
    <button
      type="button"
      class="camp-toggle"
      aria-expanded={!collapsed}
      aria-controls={collapsed ? undefined : openId}
      onclick={toggle}
    >
      <span class="disclosure" aria-hidden="true">{collapsed ? "▸" : "▾"}</span>
      <span class="camp-name"
        >{showNamespace ? `${job.namespace}/${job.name}` : job.name}</span
      >
    </button>
    {#if warmupChip !== null}
      <span class="chip warmup {job.warmup.phase}" title={warmupReason}
        >{warmupChip}</span
      >
    {/if}
    <span
      class="chip phase {job.phase.toLowerCase()}"
      class:lost={campaignLost}
      data-mix={mix}
      title={phaseTitle}
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
          {#if job.createdAt !== null}{" "}<span
              class="arrow"
              aria-hidden="true">→</span
            >{" "}{/if}<span class="sr-only"
            >{job.createdAt === null ? "finished " : ", finished "}</span
          ><time datetime={job.finishedAt} title={job.finishedAt}
            >{finishedLabel}</time
          >
        {:else if stillGoing}
          {" "}<span class="arrow" aria-hidden="true">→</span>{" "}<span
            aria-hidden="true">…</span
          ><span class="sr-only">, still running</span>
        {/if}
      </span>
    {/if}
  </div>

  <!-- Folded, the card is the line above and nothing else: a list of folded
       cards is a list of campaigns to scan (the repo owner). Everything
       below renders only while the card is open. -->
  {#if !collapsed}
    <div id={openId}>
      <!-- Zones 2 and 3, and the volumes: ONE column system. Every row below --
       the campaign's two totals and every row of the list -- is the same
       tracks, so a reader's eye runs down one column of numbers and one
       column of pills instead of three sets of columns that line up with
       nothing (the product owner, 2026-09-16). A campaign of one volume
       drops the totals: that volume's own row is already the total, said
       twice over. -->
      <div class="card-body">
        {#if showTotals}
          {@render totalsRow("volumes", volumeCell, 0)}
          {@render totalsRow("pages", pageCell, notice.errors)}
        {/if}

        <!-- Only when something is wrong, and never the numbers above. The
         sentences are real text, not a hidden copy of themselves, and they
         wrap: nothing here is cut to one line, so nothing needs a title for
         the mouse (3080). The page error's own "log" goes with it, and only
         while it is one of the sentences shown. -->
        {#if problems.length > 0}
          <p class="problems">
            <span class="problems-text" id={problemsId}
              >{#each shownProblems as part, i (i)}{i > 0
                  ? " · "
                  : ""}{#if part.id !== null}{#if part.href === null}<span
                      class="pid">{part.id}</span
                    >{:else}<a class="pid" href={part.href}>{part.id}</a
                    >{/if}{": "}{/if}{part.text}{/each}</span
            >
            {#if noticeHref !== null && shownProblems.some((p) => p.id === null)}
              <a class="problems-log" href={noticeHref}>log</a>
            {/if}
            {#if heldBack > 0}
              <button
                type="button"
                class="problems-more"
                aria-expanded={allProblems}
                aria-controls={problemsId}
                onclick={() => (allProblems = !allProblems)}
                >{allProblems ? "fewer" : `${heldBack} more`}</button
              >
            {/if}
          </p>
        {/if}
        {#if detailError !== null}
          <p class="notice error-row" role="alert">{detailError}</p>
        {/if}

        <!-- An ARIA table rather than a <table>: the rows have to be the same
           grid the totals above them are, and a real table cannot share
           those tracks. The column headers are there, just not drawn -- the
           labels above already say what the columns are. -->
        <div
          class="volumes"
          role="table"
          aria-label="Volumes in campaign {job.name}"
        >
          <div class="row head sr-only" role="row">
            <span role="columnheader">volume</span>
            <span role="columnheader">links</span>
            <span role="columnheader">progress</span>
            <span role="columnheader">pages</span>
            <span role="columnheader">status</span>
          </div>
          {#each volumes as v (v.id)}
            <div class="row volume" role="row">
              {@render volumeRow(v, "cell")}
              {@render volumeNote(v, "cell")}
            </div>
          {/each}
        </div>
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
      <!-- Zone 4, and the card's footer: which recipe and which weights produced
       these results. Provenance is checked once and read least, and in the
       header it competed with the campaign's state for the same row (the
       product owner, 2026-09-16: "pipeline and model info ... are a bit
       noisy in the header"), so it sits under the volumes, lightest of all.
       The chip is still a button and still toggles the YAML below it: two
       sibling elements, never a button inside a button. Static until the
       detail has loaded, or when the pipeline ConfigMap is gone -- there is
       nothing to toggle yet. No expander behind a count either: a real
       pipeline names two or three models
       (examples/campaigns/pipelines/demo-v1.yaml), so the whole line fits a
       normal card and clips, with its own title, on a narrow one. -->
      <p class="card-meta">
        <span class="provenance">
          pipeline
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
        </span>
        {#if models.length > 0}
          <span
            class="models"
            title="Models: {models.map(modelLabel).join(' · ')}"
          >
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
        {/if}
      </p>
      {#if yamlOpen && pipelineYaml !== ""}
        <pre class="pipeline-yaml" id={yamlId}>{pipelineYaml}</pre>
      {/if}
    </div>
  {/if}
</section>

<style>
  /* The left accent is the campaign's health at a glance: green = every
     index done, blue = a Job still running, red = a Job failed or carries a
     failed index, grey = queued/paused/nothing moving, and amber running
     into green or red for the two partial endings (below). */
  .campaign {
    /* The card body's fixed tracks, in one place, so every row -- totals
       and every row of the volume list -- is laid out on the
       same columns. Absolute units, not em: the rows do not all share a
       font-size, and a column that moved with the text would not be a
       column. The bar is short and fixed again ("loading bars are a bit
       big", the product owner, 2026-09-16); the pill's width is its own
       word slot plus its padding, written down so an empty pill cell on a
       totals row holds the column open. */
    --icons: 3.2rem;
    --bar: 6rem;
    /* "637 / 638" in tabular figures, with room to spare. */
    --fraction: 5rem;
    --pill: 5.8rem;
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

  /* Published, but not all of it: amber at the top running into where the
     campaign ended at the bottom -- green when every volume finished and
     pages were lost, red when whole volumes were. A border cannot take a
     gradient and keep its radius, so the border goes transparent over a
     second background layer painted out to the border box; the card's own
     colour fills the padding box above it. No text sits on it, so the
     blend can be smooth. */
  .campaign[data-health="partly-succeeded"] {
    --mix-to: var(--success);
  }

  .campaign[data-health="partly-failed"] {
    --mix-to: var(--destructive);
  }

  .campaign[data-health^="partly-"] {
    border-left-color: transparent;
    background:
      linear-gradient(var(--card), var(--card)) padding-box,
      linear-gradient(to bottom, var(--warning), var(--mix-to)) border-box;
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
  /* Zone 3. Warning, not error: a campaign saying this has usually
     published most of itself. It wraps: clipped to one line, the second
     failure on could not be read on a phone or from a keyboard, and a
     focused link inside the clip was invisible (3080). */
  .problems {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.15rem 0.5rem;
    margin: 0.35rem 0 0;
    font-size: 0.78rem;
    color: var(--warning);
  }

  .problems-text {
    min-width: 0;
    overflow-wrap: anywhere;
  }

  /* The rest of a long list: a quiet text button in the line's own
     colour, with the focus ring every control here wears. */
  .problems-more {
    font: inherit;
    color: inherit;
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    text-decoration: underline;
    text-underline-offset: 0.15em;
    white-space: nowrap;
  }

  .problems-more:hover {
    color: var(--primary);
  }

  .problems-more:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
    border-radius: 3px;
  }

  /* The volume's id is the link, so the sentence beside it stays a
     sentence. */
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

  /* Quieter than it was in the header: it is the last line of the card now,
     and nothing on that line should pull the eye off the volumes above it. */
  .card-meta .chip.pipeline {
    font-size: 11.5px;
    padding: 0 0.4rem;
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

  /* Zone 4, and the quietest lines on the card: provenance is what a reader
     checks once, not what they came for. A step lighter than the numbers
     above it, in the same muted colour. Two rows, the pipeline and then the
     models under it -- on one row the models line pushed the chip about as
     it wrapped (the repo owner). The models clip rather than wrap, with
     their title carrying the list the clip cut -- `clip` with a margin
     rather than `hidden`, so a focus ring on the last link is not shaved
     off. */
  .card-meta {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 0.15rem;
    margin: 0.35rem 0 0;
    font-size: 11.5px;
    font-weight: 400;
    color: var(--muted-foreground);
    opacity: 0.9;
  }

  /* The pipeline half of the footer: the word, then the chip that toggles
     the recipe behind it. */
  .provenance {
    display: inline-flex;
    align-items: baseline;
    gap: 0.35rem;
    flex-shrink: 0;
  }

  .models {
    min-width: 0;
    max-width: 100%;
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

  /* The mix, on the chip: its outline, amber on the left half and green or
     red on the right, meeting at one point. A hard split rather than a
     blend, and on the edge rather than under the word, so the pale fill
     and the text's contrast are exactly the amber chip's in both themes.
     A border rather than a two-colour dot: the dot on this chip already
     means "running", and at half an em a dot split in two is too small to
     read as two colours. The border's width comes out of the padding, so
     the chip is the size every other chip is. */
  .chip.phase[data-mix="success"] {
    --mix-to: var(--success);
  }

  .chip.phase[data-mix="destructive"] {
    --mix-to: var(--destructive);
  }

  .chip.phase[data-mix] {
    border: 1.5px solid transparent;
    padding: calc(0.1rem - 1.5px) calc(0.5rem - 1.5px);
    background:
      linear-gradient(var(--warning-soft), var(--warning-soft)) padding-box,
      linear-gradient(90deg, var(--warning) 50%, var(--mix-to) 50%) border-box;
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

  /* ONE column system for everything the card counts. Every row -- the
     campaign's totals and every row of the volume list -- declares the same four tracks from the same custom properties, so
     they coincide exactly without a subgrid and without one table's layout
     leaking into another's. Track 1 flexes (it holds a label or a volume
     id); the other three are fixed, which is what makes a column of numbers
     a column across ten cards rather than per card. */
  .card-body {
    margin-top: 0.4rem;
    font-size: 0.78rem;
    line-height: 1.4;
    color: var(--muted-foreground);
  }

  .row {
    display: grid;
    /* Five tracks, and the LABEL is the one that stretches: the words -- a
       campaign\'s "volumes", a volume\'s id, and what failed in it -- take
       the left, where there is room for them, and the three fixed things
       pack against the right in the order a reader wants them: the bar, the
       fraction it draws, and the state it ended in (the product owner,
       2026-09-16: "the X / Y should be in between of these; it\'s fine if
       the \'X failed\' is placed after the volume name"). */
    grid-template-columns:
      minmax(6rem, 1fr) var(--icons) var(--bar) var(--fraction)
      var(--pill);
    align-items: center;
    column-gap: 0.75rem;
    padding: 0.12rem 0;
  }

  /* A hairline between volumes, so a long list reads as rows. */
  .row.volume {
    border-top: 1px solid var(--border);
  }

  /* The volume's own sentence, under its own row and across all of it. */
  .row-note {
    grid-column: 1 / -1;
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
    margin: 0;
    padding-left: 1rem;
    font-size: 0.74rem;
    color: var(--warning);
    min-width: 0;
  }

  /* Wraps, like zone 3 and for the same reason (3080). */
  .row-note-text {
    min-width: 0;
    overflow-wrap: anywhere;
  }

  /* `clip` with a margin rather than `hidden`, here and on the id's line
     below: an over-long id is still cut, but the focus ring of the link
     inside it is drawn whole (3080). */
  .c-label {
    min-width: 0;
    overflow: clip;
    overflow-clip-margin: 4px;
    font-weight: 500;
    color: var(--foreground);
  }

  /* The id, on one line that clips. */
  .vid-line {
    display: flex;
    align-items: baseline;
    min-width: 0;
    max-width: 100%;
    white-space: nowrap;
    overflow: clip;
    overflow-clip-margin: 4px;
  }

  .row.totals .c-label {
    font-weight: 400;
    color: var(--muted-foreground);
  }

  /* What the row lost, under its own bar: its own line in the bar's column,
     right-aligned to it, so a clean row stays one line. */
  .c-lost {
    grid-column: 3;
    justify-self: end;
    font-size: 0.7rem;
    color: var(--destructive);
    white-space: nowrap;
  }

  .quiet {
    font-weight: 400;
    color: var(--muted-foreground);
  }

  /* The one column of numbers on the card, between the bar and the pill. */
  .c-fraction {
    min-width: 0;
    text-align: right;
    font-variant-numeric: tabular-nums;
    color: var(--foreground);
    white-space: nowrap;
  }

  /* The icons sit at the left of their own track, the pill at the right of
     its. Nothing here is centred -- both edges are anchored. */
  .c-links {
    display: flex;
    align-items: center;
  }

  .c-status {
    display: flex;
    align-items: center;
    justify-content: flex-end;
  }

  .volumes {
    margin-top: 0;
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
  /* The figures, in zone 2's shape and zone 2's colours: the same fraction
     and the same `.bad` count, so a volume's row and the campaign's line
     above it read as one column of numbers (2026-09-16). */
  /* The figures themselves; the track they sit in is what fixes their
     width, so a poll that changes the digits moves nothing. */
  .vfigures {
    white-space: nowrap;
  }

  /* The widest state word is "pending"/"unknown"; holding the slot keeps
     every pill the same width, so its left edge stops wandering from row to
     row (the product owner, 2026-09-16). */
  .status-word {
    display: inline-block;
    min-width: 4.6em;
  }

  /* What the numbers cannot say: the stage, and how long ago. */
  .vprogress {
    display: block;
    font-size: 11.5px;
    color: var(--muted-foreground);
    overflow-wrap: anywhere;
  }

  /* The bar has a track of its own now, so it needs no width and no margin
     of its own: it fills the column on every row that has one, and the cell
     may shrink to nothing rather than push the bar past its track. */
  .c-bar {
    min-width: 0;
  }

  .c-bar .bar {
    margin-top: 0;
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

  /* Each volume's bar in that volume's own colour, the same tokens its
     state word takes: published-but-not-all-of-it amber, finished green,
     failed red. The default is the running blue. */
  .fill.lost {
    background: var(--warning);
  }

  .fill.done {
    background: var(--success);
  }

  .fill.failed {
    background: var(--destructive);
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
  .vfigures.bump {
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

  .links {
    display: inline-flex;
    align-items: center;
    white-space: nowrap;
  }

  /* A fixed slot each, so a volume with no source manifest leaves a gap
     rather than shifting the row beside it. Left-aligned in the track:
     the pair starts where the track starts. */
  .slot {
    display: inline-flex;
    justify-content: flex-start;
    width: 1.6rem;
  }

  /* 24px of hit area around a 14px glyph: the icon is small, the target is
     not (2026-09-16). currentColor, so both themes get it for free. */
  .vicon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 24px;
    min-height: 24px;
    color: var(--muted-foreground);
    text-decoration: none;
    border-radius: 3px;
  }

  .vicon:hover {
    color: var(--primary);
  }

  .vicon:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 1px;
    color: var(--primary);
  }

  a.vid-name {
    color: var(--primary);
    text-decoration: none;
  }

  a.vid-name:hover {
    text-decoration: underline;
  }

  a.vid-name:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
    border-radius: 3px;
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
    /* Still one column system, folded to two lines. Line 1 is the id and
       what failed in it, across the width; line 2 is the icons, the short
       bar, the fraction and the pill. On the live phone the figures clipped
       to "5 / 6 · 1 f" and the bar ran on under the icons (the product
       owner, 2026-09-16), so the words wrap here rather than clip.

       Line 2's four fixed tracks plus their gaps are wider than a 390px
       card, and a squeezed grid takes the width back from whichever item
       can give it: on a volume row that was the bar, the only cell whose
       content has no width of its own, so it collapsed to nothing while
       the totals rows -- whose icon and pill cells are empty and could
       give instead -- kept theirs. The bar has a floor now and may shrink
       between it and its full width, and the leading spacer is gone so
       line 2 has the whole card to lay out in. */
    .row {
      grid-template-columns:
        0 var(--icons) minmax(2.5rem, var(--bar)) var(--fraction)
        var(--pill);
      grid-template-areas:
        "label label label     label    label"
        ".     links bar       fraction status"
        ".     .     lost      lost     lost"
        "note  note  note      note     note";
      column-gap: 0.5rem;
      row-gap: 0.2rem;
      padding: 0.35rem 0;
    }

    .c-label {
      grid-area: label;
    }

    .c-links {
      grid-area: links;
    }

    /* Never squeezed away: a row without its bar is the defect this fixes. */
    .c-bar {
      grid-area: bar;
      align-self: center;
      min-width: 2.5rem;
    }

    .c-bar .bar {
      width: 100%;
    }

    .c-status {
      grid-area: status;
    }

    .c-fraction {
      grid-area: fraction;
      min-width: 0;
    }

    .c-lost {
      grid-area: lost;
      justify-self: end;
    }

    /* Wraps rather than clips: the id and what failed in it are the words
       of the row, and there is a whole line for them here. */
    .vid-line {
      white-space: normal;
      overflow: visible;
    }

    .row-note {
      grid-area: note;
      padding-left: 0;
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
    .vfigures.bump {
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
