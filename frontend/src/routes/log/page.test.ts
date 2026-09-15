import { render, screen } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { LIVE_MAX_FAILURES, LIVE_MS } from "$lib/config.js";
import { MAX_POLL_MS } from "$lib/poll.js";
import LogPage from "./+page.svelte";

function fetch404(): typeof fetch {
  return vi.fn(
    async () => new Response("no such key", { status: 404 }),
  ) as typeof fetch;
}

describe("/log live mode", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.history.replaceState(
      null,
      "",
      "/log?log=http://bucket/logs/v1.txt&live=1",
    );
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
  });

  test("keeps polling through 404s, backs off, then gives up and says so", async () => {
    const fetchMock = vi.fn(fetch404());
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(LogPage);
    await vi.advanceTimersByTimeAsync(0);
    // first miss: still live, no error shown
    expect(screen.getByRole("status")).toHaveTextContent("live");
    expect(screen.queryByRole("alert")).toBeNull();

    // A log that is not there yet is no longer asked for on the live
    // cadence for ever: nineteen live periods no longer buy nineteen
    // requests (2026-09-14 audit).
    await vi.advanceTimersByTimeAsync(LIVE_MS * (LIVE_MAX_FAILURES - 1));
    expect(fetchMock.mock.calls.length).toBeLessThan(LIVE_MAX_FAILURES);
    expect(screen.queryByRole("alert")).toBeNull();

    // Long enough for twenty attempts even at the backoff ceiling.
    await vi.advanceTimersByTimeAsync(MAX_POLL_MS * LIVE_MAX_FAILURES);
    expect(fetchMock).toHaveBeenCalledTimes(LIVE_MAX_FAILURES);
    expect(screen.getByRole("alert")).toHaveTextContent(
      `gave up after ${LIVE_MAX_FAILURES} failed polls (HTTP 404)`,
    );
    expect(screen.getByRole("status")).toHaveTextContent("stopped");

    // and the poll is gone: no further fetches, and no last read that
    // would overwrite the sentence above with a bare "HTTP 404".
    await vi.advanceTimersByTimeAsync(MAX_POLL_MS * 3);
    expect(fetchMock).toHaveBeenCalledTimes(LIVE_MAX_FAILURES);
  });

  test("a non-http log URL is refused before any fetch", async () => {
    window.history.replaceState(null, "", "/log?log=javascript:alert(1)");
    const fetchMock = fetch404();
    vi.stubGlobal("fetch", fetchMock);
    render(LogPage);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "must be an absolute http(s) URL",
    );
    expect(screen.queryByRole("link", { name: "raw" })).toBeNull();
  });
});
