// One poller for every page that keeps itself up to date: the campaign
// list, each card's own volume table, and the live run log.
//
// Three rules, all of them about a page nobody is watching, or a service
// that is not answering (the 2026-09-14 audit, F3/F14). A tick that is
// still in flight is not joined by the next one — a slow API turned one
// card into a queue of overlapping requests, and with a card per campaign
// that is a pile-up the browser never gets out of. Nothing polls while the
// tab is in the background, and coming back to it polls at once rather than
// showing a stale page until the next tick. And a run of failures doubles
// the wait, up to a ceiling: an API that is down should not be asked sixty
// times a minute by every open tab.

/** Longest a backed-off poll waits between attempts. */
export const MAX_POLL_MS = 300_000;

/** `true` when the tick got what it asked for; `false` starts the backoff. */
export type Tick = (signal: AbortSignal) => Promise<boolean>;

/** Whether nobody is looking. Absent outside a browser (SSR, a test). */
function hidden(): boolean {
  return typeof document !== "undefined" && document.hidden;
}

/**
 * Run `tick` now and then every `period` ms. Returns the stopper: it aborts
 * the tick in flight and schedules nothing more, which is exactly what a
 * Svelte `$effect` cleanup needs.
 *
 * `until` is asked after every tick, and `true` ends the poll there. It is
 * how a poll whose own tick learns that nothing will change again -- a run
 * log that reached its last line, a campaign that finished -- stops without
 * the caller tearing it down from outside: an outside stop aborts the tick
 * that is still finishing its work (the 2026-09-17 audit, 3077).
 */
export function startPolling(
  tick: Tick,
  period: number,
  { until }: { until?: () => boolean } = {},
): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let inflight: AbortController | null = null;
  let failures = 0;
  let stopped = false;

  function wait(): number {
    return Math.min(period * 2 ** failures, MAX_POLL_MS);
  }

  function schedule(): void {
    clearTimeout(timer);
    if (!stopped) timer = setTimeout(() => void run(), wait());
  }

  async function run(): Promise<void> {
    // Already asking, or nobody to show the answer to: try again later.
    if (stopped || inflight !== null || hidden()) {
      schedule();
      return;
    }
    const controller = new AbortController();
    inflight = controller;
    try {
      failures = (await tick(controller.signal)) ? 0 : failures + 1;
    } catch {
      // A tick that let something escape is a tick that failed, not the
      // end of the poll: before this, one bug in a handler stopped the page
      // updating for good and said nothing (2026-09-14 review).
      failures += 1;
    } finally {
      inflight = null;
    }
    if (!stopped && until?.()) stop();
    schedule();
  }

  function onVisible(): void {
    if (!hidden()) void run();
  }

  function stop(): void {
    stopped = true;
    clearTimeout(timer);
    inflight?.abort();
    if (typeof document !== "undefined")
      document.removeEventListener("visibilitychange", onVisible);
  }

  if (typeof document !== "undefined")
    document.addEventListener("visibilitychange", onVisible);
  void run();

  return stop;
}

/**
 * At most `max` tasks at once; the rest wait their turn, in the order they
 * asked. Every folded card on screen reads its own detail, and a page of
 * fifty campaigns asked the API for fifty at the same instant; the cards
 * share one of these. A task whose `signal` aborts while it waits never
 * runs, and its promise rejects the way an aborted fetch does.
 */
export function gate(max: number) {
  let running = 0;
  const waiting: (() => void)[] = [];
  return async function through<T>(
    task: () => Promise<T>,
    signal?: AbortSignal,
  ): Promise<T> {
    // A finished task hands its place straight to the next in line rather
    // than freeing it, so nothing that asks in between can jump the queue
    // and put one more in flight than `max`.
    if (running < max) running += 1;
    else
      await new Promise<void>((go, stop) => {
        const turn = () => {
          signal?.removeEventListener("abort", leave);
          go();
        };
        const leave = () => {
          waiting.splice(waiting.indexOf(turn), 1);
          stop(new DOMException("aborted while waiting", "AbortError"));
        };
        waiting.push(turn);
        signal?.addEventListener("abort", leave, { once: true });
      });
    try {
      return await task();
    } finally {
      const turn = waiting.shift();
      if (turn === undefined) running -= 1;
      else turn();
    }
  };
}
