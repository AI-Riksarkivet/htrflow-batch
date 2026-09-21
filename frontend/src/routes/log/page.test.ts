import { fireEvent, render, screen } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { LIVE_MAX_FAILURES, LIVE_MS } from "$lib/config.js";
import { MAX_POLL_MS } from "$lib/poll.js";
import { LOG_TAIL_BYTES } from "$lib/runlog.js";
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

  test("the poll that sees the last line still reads the manifest (3077)", async () => {
    // The wrapper writes manifest.json before it logs COMPLETE, so the one
    // poll that finds the terminal line finds the manifest too. Ending the
    // live mode used to abort that same poll before its manifest fetch ran,
    // and a volume that finished while someone watched never got its
    // summary (the 2026-09-17 audit).
    window.history.replaceState(
      null,
      "",
      "/log?log=http://bucket/logs/v1.txt&manifest=http://bucket/v1/manifest.json&live=1",
    );
    const manifest = {
      volume: "v1",
      pipeline_id: "p",
      htrflow_version: "0.2.6",
      image_digest: "reg/img@sha256:abcdef0123456789",
      pages: 1,
      results: { "0001": { status: "ok", seconds: 1 } },
    };
    // Refuses an aborted signal the way a browser's fetch does.
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.signal?.aborted) throw new DOMException("", "AbortError");
      return url.endsWith("manifest.json")
        ? new Response(JSON.stringify(manifest))
        : new Response(
            "2026-09-08 09:00:00,000 INFO [v1] COMPLETE 1 pages " +
              "(1 processed, 0 failed) in 1.0s, viewer: x\n",
          );
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(LogPage);
    await vi.advanceTimersByTimeAsync(0);
    expect(
      screen.getByRole("heading", { name: "Run log · v1" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("finished");
    // and it is over: nothing more is asked for.
    const calls = fetchMock.mock.calls.length;
    await vi.advanceTimersByTimeAsync(LIVE_MS * 5);
    expect(fetchMock).toHaveBeenCalledTimes(calls);
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

describe("/log with a very large log", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.history.replaceState(null, "", "/log?log=http://bucket/logs/v1.txt");
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
  });

  const head = "2026-09-08 09:00:00,000 INFO the first line\n";
  const tail = "2026-09-08 09:59:59,000 INFO the last line\n";
  const huge = head + "x".repeat(LOG_TAIL_BYTES) + "\n" + tail;

  function serve(text: string): void {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(text)) as typeof fetch,
    );
  }

  test("only the end is drawn, and the page says so", async () => {
    serve(huge);
    render(LogPage);
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByText(/Showing the end of this log/)).toBeInTheDocument();
    expect(screen.getByText("the last line")).toBeInTheDocument();
    expect(screen.queryByText("the first line")).toBeNull();
  });

  test("the whole log is one click away", async () => {
    serve(huge);
    render(LogPage);
    await vi.advanceTimersByTimeAsync(0);
    await fireEvent.click(
      screen.getByRole("button", { name: "show whole log" }),
    );
    expect(screen.getByText("the first line")).toBeInTheDocument();
    expect(screen.queryByText(/Showing the end of this log/)).toBeNull();
  });

  test("an ordinary log is drawn whole with no control at all", async () => {
    serve(head + tail);
    render(LogPage);
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByText("the first line")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "show whole log" })).toBeNull();
  });
});

describe("/log only opens this deployment's own results", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    delete window.RESULTS_BASE;
    window.history.replaceState(null, "", "/");
  });

  test("a log URL outside the results base is refused before any fetch", async () => {
    window.RESULTS_BASE = "https://results.example.org/bucket";
    window.history.replaceState(
      null,
      "",
      "/log?log=https://evil.example.org/x.txt",
    );
    const fetchMock = fetch404();
    vi.stubGlobal("fetch", fetchMock);
    render(LogPage);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "in the results bucket",
    );
  });

  test("a log URL inside it is fetched as before", async () => {
    const base = "https://results.example.org/bucket";
    window.RESULTS_BASE = base;
    window.history.replaceState(
      null,
      "",
      `/log?log=${base}/status/logs/d/v.txt`,
    );
    const fetchMock = vi.fn(
      async () => new Response("2026-09-08 09:00:00,000 INFO hi\n"),
    );
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(LogPage);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledWith(
      `${base}/status/logs/d/v.txt`,
      expect.objectContaining({ cache: "no-cache" }),
    );
  });
});
