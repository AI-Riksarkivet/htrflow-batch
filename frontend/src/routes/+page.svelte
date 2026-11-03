<script lang="ts">
  import AppHeader from "#lib/components/AppHeader.svelte";
  import CampaignCard from "#lib/components/CampaignCard.svelte";
  // fetchJobs reads GET /api/v1/jobs (the read API); RELOAD_MS is the poll
  // cadence, both documented in #lib/config / #lib/api.
  import {
    fetchJobs,
    moreReaped,
    NotLoggedIn,
    REAPED_PAGE,
    reapedHidden,
    type JobSummary,
  } from "#lib/api.js";
  import { RELOAD_MS } from "#lib/config.js";
  import { byAttention, keepOrder, outOfOrder } from "#lib/order.js";
  import { startPolling } from "#lib/poll.js";
  import { describeApiError, describeUnreadable } from "#lib/reasons.js";
  import { untrack } from "svelte";
  import { flip } from "svelte/animate";

  // The last good list stays on screen through a failed poll; `error` is a
  // banner on top of it, never a replacement for it.
  let jobs = $state<JobSummary[] | null>(null);
  let unreadable = $state(0);
  // The count the reader put its banner away at: it stays away until the
  // count changes, since the rows can stay unreadable for good.
  let putAway = $state(0);
  // What the last poll failed with, and when the next one is: the banner's
  // sentence is made of both, so it names the real next try.
  let failure = $state<unknown>(null);
  let retryAt = $state<Date | undefined>(undefined);
  const error = $derived(
    failure === null ? null : describeApiError(failure, jobs !== null, retryAt),
  );

  // Campaigns whose Jobs are gone: the API sends the newest `reapedShown`
  // of them and says how many there are. Their records have no TTL, so
  // without a window every campaign ever run was a card here (the
  // 2026-09-23 audit); the rest are a button away, a page at a time.
  let reapedShown = $state(REAPED_PAGE);
  let reapedTotal = $state(0);
  const olderHidden = $derived(reapedHidden(reapedTotal, reapedShown));

  // The read API serves its own namespace unless configured with more, and
  // a prefix every card carries tells nobody anything. Said on every card
  // only when the list spans namespaces, where it tells two campaigns apart.
  const showNamespace = $derived(
    new Set((jobs ?? []).map((j) => j.namespace)).size > 1,
  );

  // A poll keeps the order the reader has, so a campaign that started, or
  // finished, stays where it was. Moving it on its own would be a jump
  // under someone reading; leaving it for good would bury what the order is
  // for. The dock says so and offers the sort; the reader decides when.
  // (One that falls into trouble moves at once: #lib/order.)
  const drifted = $derived(jobs !== null && outOfOrder(jobs));

  // The glide is an animation the page runs itself (the Web Animations
  // API), which app.css's reduced-motion rule does not reach: asked for
  // stillness, a moved card is simply in its new place.
  const still =
    typeof matchMedia === "function" &&
    matchMedia("(prefers-reduced-motion: reduce)").matches;

  // How tall the dock at the foot of the window is, so the page keeps that
  // much room under its last card and nothing stays covered.
  let dockHeight = $state(0);

  // Set when the reader comes back to the tab: the next answer is sorted
  // afresh rather than kept in the order they left it in. Set on the
  // return, not on leaving: an answer that lands while the tab is hidden
  // must not use the re-sort up on a list nobody is looking at.
  let resort = false;

  $effect(() => {
    const back = () => {
      if (!document.hidden) resort = true;
    };
    document.addEventListener("visibilitychange", back);
    return () => document.removeEventListener("visibilitychange", back);
  });

  // One request in flight at a time, nothing polled while the tab is in the
  // background, and a run of failures backing off — all of it in
  // #lib/poll, so the list, each card and the run log cannot disagree.
  // Set once a 401 has sent the browser to /login: nothing more is asked.
  let leaving = false;

  async function load(signal: AbortSignal): Promise<boolean> {
    try {
      const result = await fetchJobs(signal, reapedShown);
      if (signal.aborted) return true;
      reapedTotal = result.reapedTotal;
      // The API sorts by creation date; the page sorts by what wants a
      // person (see #lib/order) -- once. A poll keeps the order the reader
      // has and only adds to it, so no card moves under them; the list is
      // sorted afresh when they come back to it from another tab, when
      // there is nobody reading it to move anything under.
      jobs =
        jobs === null || resort
          ? byAttention(result.jobs)
          : keepOrder(jobs, result.jobs);
      resort = false;
      unreadable = result.unreadable;
      failure = null;
      return true;
    } catch (e) {
      if (signal.aborted) return true;
      if (e instanceof NotLoggedIn) return (leaving = true);
      // One sentence for the reader: what is wrong, that the list on
      // screen is the older one, and that it retries on its own. The
      // transport detail (a fetch error string, a ZodError) never reaches
      // the banner — see #lib/reasons.
      failure = e;
      return false;
    }
  }

  // Tracked: asking for more older campaigns restarts the poll, whose first
  // tick is immediate.
  $effect(() => {
    void reapedShown;
    return untrack(() =>
      startPolling(load, RELOAD_MS, {
        until: () => leaving,
        onWait: (ms) => (retryAt = new Date(Date.now() + ms)),
      }),
    );
  });
</script>

<!-- Busy until the first answer, which is when there is a list to read. -->
<main aria-busy={jobs === null && error === null} style:--dock="{dockHeight}px">
  <!-- One header on every page (AppHeader): the search field here only
       navigates to /search, which owns the searching itself. -->
  <AppHeader />
  <!-- With no list yet there is nothing to push, and the banner sits where
       the list would be; over a list it is in the dock below. -->
  {#if jobs === null && error !== null}
    <p class="banner error" role="alert">{error}</p>
  {/if}
  {#if jobs === null}
    {#if error === null}<p class="loading">Loading…</p>{/if}
  {:else if jobs.length === 0}
    <p class="empty">No campaigns.</p>
  {:else}
    <!-- A card that moves -- one that fell into trouble, a new one above
         it, the reader's re-sort -- glides to its place rather than
         jumping there, and the cards it passes glide with it. -->
    {#each jobs as job (job.namespace + "/" + job.name)}
      <div animate:flip={{ duration: still ? 0 : 300 }}>
        <CampaignCard {job} {showNamespace} />
      </div>
    {/each}
  {/if}
  {#if jobs !== null && olderHidden > 0}
    <p class="older">
      <button
        type="button"
        onclick={() => (reapedShown = moreReaped(reapedShown))}
        >show {Math.min(olderHidden, REAPED_PAGE)} of {olderHidden} older campaigns</button
      >whose Jobs have been removed
    </p>
  {/if}
  <!-- The dock: fixed to the foot of the window, so what arrives in it on
       a poll -- a banner, the offer to re-sort -- pushes no card. In the
       page, a banner pushed every card down by its own height. -->
  <div class="dock" bind:clientHeight={dockHeight}>
    {#if jobs !== null && error !== null}
      <p class="banner error" role="alert">{error}</p>
    {:else if jobs !== null && unreadable > 0 && unreadable !== putAway}
      <p class="banner error" role="alert">
        {describeUnreadable(unreadable)}
        <button
          type="button"
          class="put-away"
          aria-label="put away until the count changes"
          onclick={() => (putAway = unreadable)}>×</button
        >
      </p>
    {/if}
    {#if drifted}
      <p class="drift">
        The campaigns' order has changed.
        <button type="button" onclick={() => (jobs = byAttention(jobs ?? []))}
          >re-sort</button
        >
      </p>
    {/if}
  </div>
</main>

<style>
  .banner {
    padding: 0.5rem 1rem;
    border-radius: var(--radius);
    margin: 0 0 1rem;
  }

  /* The page's own column, pinned to the foot of the window. */
  .dock {
    position: fixed;
    z-index: 1;
    bottom: 1rem;
    left: max(1rem, calc(50vw - 32rem));
    right: max(1rem, calc(50vw - 32rem));
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .dock > p {
    margin: 0;
    box-shadow: 0 4px 16px oklch(0 0 0 / 0.18);
  }

  .put-away {
    float: right;
    margin-left: 0.5rem;
    font: inherit;
    color: inherit;
    background: none;
    border: none;
    cursor: pointer;
  }

  .drift {
    align-self: center;
    padding: 0.35rem 0.5rem 0.35rem 1rem;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--card);
    font-size: 0.85rem;
  }

  /* The same quiet pill as "show older campaigns". */
  .drift button,
  .older button {
    font: inherit;
    color: var(--foreground);
    background: var(--muted);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 0.15rem 0.75rem;
    cursor: pointer;
  }

  /* Room under the last card for whatever the dock holds, as tall as it
     is: a phone's banner wraps to four lines. */
  main {
    padding-bottom: calc(3rem + var(--dock, 0px));
  }

  .error {
    border: 1px solid var(--destructive);
    background: var(--destructive-soft);
  }

  /* Shown only once the answer is late: a list that arrives quickly then
     replaces nothing a reader saw. */
  .loading {
    color: var(--muted-foreground);
    animation: late 0s 400ms both;
  }

  @keyframes late {
    from {
      visibility: hidden;
    }
  }

  .empty {
    color: var(--muted-foreground);
  }

  /* The card's own "load more" look: the same kind of control. */
  .older {
    color: var(--muted-foreground);
    font-size: 0.9rem;
  }

  .older button {
    margin-right: 0.5rem;
  }
</style>
