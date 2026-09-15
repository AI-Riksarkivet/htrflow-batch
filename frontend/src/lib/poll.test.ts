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
    // The first failure doubles the wait: the period alone buys nothing.
    await vi.advanceTimersByTimeAsync(PERIOD);
    expect(run).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(PERIOD);
    expect(run).toHaveBeenCalledTimes(2);
    // Long enough for the ceiling to have been reached many times over.
    await vi.advanceTimersByTimeAsync(MAX_POLL_MS * 20);
    const calls = run.mock.calls.length;
    await vi.advanceTimersByTimeAsync(MAX_POLL_MS);
    expect(run.mock.calls.length).toBe(calls + 1);
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
});
