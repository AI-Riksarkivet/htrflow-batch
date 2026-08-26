import { render, screen } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import LogPage from "./+page.svelte";

const LIVE_MS = 15_000;

function fetch404(): typeof fetch {
  return vi.fn(async () => new Response("no such key", { status: 404 })) as typeof fetch;
}

describe("/log live mode", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.history.replaceState(null, "", "/log?log=http://bucket/logs/v1.txt&live=1");
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
  });

  test("keeps polling through 404s, then gives up after 20 and surfaces the error", async () => {
    const fetchMock = fetch404();
    vi.stubGlobal("fetch", fetchMock);
    render(LogPage);
    await vi.advanceTimersByTimeAsync(0);
    // first miss: still live, no error shown
    expect(screen.getByRole("status")).toHaveTextContent("live");
    expect(screen.queryByRole("alert")).toBeNull();

    await vi.advanceTimersByTimeAsync(LIVE_MS * 18);
    expect(fetchMock).toHaveBeenCalledTimes(19);
    expect(screen.queryByRole("alert")).toBeNull();

    await vi.advanceTimersByTimeAsync(LIVE_MS);
    expect(fetchMock).toHaveBeenCalledTimes(20);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "gave up after 20 failed polls (HTTP 404)",
    );
    expect(screen.getByRole("status")).toHaveTextContent("stopped");

    // and the interval is gone: no further fetches
    await vi.advanceTimersByTimeAsync(LIVE_MS * 3);
    expect(fetchMock).toHaveBeenCalledTimes(20);
  });

  test("a non-http log URL is refused before any fetch", async () => {
    window.history.replaceState(null, "", "/log?log=javascript:alert(1)");
    const fetchMock = fetch404();
    vi.stubGlobal("fetch", fetchMock);
    render(LogPage);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("must be an absolute http(s) URL");
    expect(screen.queryByRole("link", { name: "raw" })).toBeNull();
  });
});
