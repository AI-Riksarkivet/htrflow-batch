import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { MAX_POLL_MS, startPolling } from "./poll.js";

const PERIOD = 1000;

/** Lets a test hold a tick open and settle it when it chooses. */
function deferred(): {
  promise: Promise<boolean>;
  done: (ok: boolean) => void;
} {
  let done!: (ok: boolean) => void;
  const promise = new Promise<boolean>((resolve) => (done = resolve));
  return { promise, done };
}

function setHidden(hidden: boolean): void {
  Object.defineProperty(document, "hidden", {
    configurable: true,
    get: () => hidden,
  });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("startPolling", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    setHidden(false);
  });

  test("runs once straight away and then on the period", async () => {
    const run = vi.fn(async () => true);
    const stop = startPolling(run, PERIOD);
    await vi.advanceTimersByTimeAsync(0);
    expect(run).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(PERIOD);
    expect(run).toHaveBeenCalledTimes(2);
    stop();
  });

  test("a tick that is still in flight is not joined by the next one", async () => {
    const pending = deferred();
    const run = vi.fn(() => pending.promise);
    const stop = startPolling(run, PERIOD);
    await vi.advanceTimersByTimeAsync(PERIOD * 5);
    expect(run).toHaveBeenCalledTimes(1);
    pending.done(true);
    await vi.advanceTimersByTimeAsync(PERIOD);
    expect(run).toHaveBeenCalledTimes(2);
    stop();
  });

  test("nothing is polled while the tab is in the background", async () => {
    const run = vi.fn(async () => true);
    const stop = startPolling(run, PERIOD);
    await vi.advanceTimersByTimeAsync(0);
    setHidden(true);
    await vi.advanceTimersByTimeAsync(PERIOD * 10);
    expect(run).toHaveBeenCalledTimes(1);
    stop();
  });

  test("coming back to the tab polls at once instead of waiting", async () => {
    const run = vi.fn(async () => true);
    const stop = startPolling(run, PERIOD);
    await vi.advanceTimersByTimeAsync(0);
    setHidden(true);
    await vi.advanceTimersByTimeAsync(PERIOD * 3);
    setHidden(false);
    await vi.advanceTimersByTimeAsync(0);
    expect(run).toHaveBeenCalledTimes(2);
    stop();
  });

  test("consecutive failures double the wait, up to the ceiling", async () => {
    const run = vi.fn(async () => false);
    const stop = startPolling(run, PERIOD);
    await vi.advanceTimersByTimeAsync(0);
    expect(run).toHaveBeenCalledTimes(1);
    // Each wait to the millisecond: nothing a millisecond early, one call on
    // time. The first failure already doubles it -- the period alone buys
    // nothing -- and the ceiling, once reached, is the wait from then on.
    const waits = [2, 4, 8, 16, 32, 64, 128, 256].map((n) => n * PERIOD);
    for (const wait of [...waits, MAX_POLL_MS, MAX_POLL_MS]) {
      const calls = run.mock.calls.length;
      await vi.advanceTimersByTimeAsync(wait - 1);
      expect(run.mock.calls.length, `before ${wait} ms`).toBe(calls);
      await vi.advanceTimersByTimeAsync(1);
      expect(run.mock.calls.length, `at ${wait} ms`).toBe(calls + 1);
    }
    stop();
  });

  test("one success puts the cadence back", async () => {
    let ok = false;
    const run = vi.fn(async () => ok);
    const stop = startPolling(run, PERIOD);
    await vi.advanceTimersByTimeAsync(PERIOD * 8);
    ok = true;
    await vi.advanceTimersByTimeAsync(MAX_POLL_MS);
    const calls = run.mock.calls.length;
    await vi.advanceTimersByTimeAsync(PERIOD);
    expect(run.mock.calls.length).toBe(calls + 1);
    stop();
  });

  test("a tick that throws is a failed tick, not the end of the poll", async () => {
    // A caller that lets something escape -- a bug in a handler, a schema
    // that threw where nothing catches it -- used to stop the page updating
    // for good, silently (2026-09-14 review).
    let thrown = 0;
    const run = vi.fn(async () => {
      thrown += 1;
      throw new Error("boom");
    });
    const stop = startPolling(run, PERIOD);
    await vi.advanceTimersByTimeAsync(0);
    expect(thrown).toBe(1);
    // Backed off like any other failure: the period alone buys nothing.
    await vi.advanceTimersByTimeAsync(PERIOD * 2);
    expect(thrown).toBe(2);
    await vi.advanceTimersByTimeAsync(MAX_POLL_MS * 5);
    expect(thrown).toBeGreaterThan(2);
    stop();
  });

  test("a tick that throws once does not poison the ones after it", async () => {
    let calls = 0;
    const run = vi.fn(async () => {
      calls += 1;
      if (calls === 1) throw new Error("boom");
      return true;
    });
    const stop = startPolling(run, PERIOD);
    await vi.advanceTimersByTimeAsync(PERIOD * 2);
    expect(calls).toBe(2);
    await vi.advanceTimersByTimeAsync(PERIOD);
    expect(calls).toBe(3); // back on the plain cadence
    stop();
  });

  test("stopping aborts the tick in flight and schedules nothing more", async () => {
    let seen: AbortSignal | undefined;
    const pending = deferred();
    const stop = startPolling((signal) => {
      seen = signal;
      return pending.promise;
    }, PERIOD);
    await vi.advanceTimersByTimeAsync(0);
    expect(seen?.aborted).toBe(false);
    stop();
    expect(seen?.aborted).toBe(true);
    pending.done(true);
    await vi.advanceTimersByTimeAsync(PERIOD * 10);
  });

  test("`until` ends the poll after the tick that made it true", async () => {
    let over = false;
    const run = vi.fn(async () => {
      if (run.mock.calls.length === 2) over = true;
      return true;
    });
    startPolling(run, PERIOD, { until: () => over });
    await vi.advanceTimersByTimeAsync(PERIOD * 10);
    expect(run).toHaveBeenCalledTimes(2);
  });

  test("`until` never aborts the tick that is finishing", async () => {
    // The tick that learns the poll is over still has work to do after it
    // learns it (the run log's last line, then its manifest; 3077).
    let over = false;
    let abortedAtEnd: boolean | undefined;
    startPolling(
      async (signal) => {
        over = true;
        await Promise.resolve();
        abortedAtEnd = signal.aborted;
        return true;
      },
      PERIOD,
      { until: () => over },
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(abortedAtEnd).toBe(false);
  });

  test("`until` is asked after a failed tick too", async () => {
    const run = vi.fn(async () => false);
    startPolling(run, PERIOD, { until: () => true });
    await vi.advanceTimersByTimeAsync(MAX_POLL_MS * 2);
    expect(run).toHaveBeenCalledTimes(1);
  });

  test("an ended poll does not wake up when the tab comes back", async () => {
    const run = vi.fn(async () => true);
    startPolling(run, PERIOD, { until: () => true });
    await vi.advanceTimersByTimeAsync(0);
    setHidden(true);
    setHidden(false);
    await vi.advanceTimersByTimeAsync(PERIOD);
    expect(run).toHaveBeenCalledTimes(1);
  });
});
