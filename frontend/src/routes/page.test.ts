import { render, screen } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { RELOAD_MS } from "$lib/config.js";
import CampaignsPage from "./+page.svelte";

const job = {
  namespace: "htr-test",
  name: "kyrk",
  pipeline: "demo-v1",
  phase: "Running",
  counts: { total: 7, active: 1, done: 4, failed: 1 },
  suspended: false,
  createdAt: "2026-01-01T00:00:00Z",
  finishedAt: null,
  resultsBase: "https://results.example.org/htr-test/demo-v1",
  warmup: { phase: "succeeded" },
  jobGone: false,
};

const detail = {
  ...job,
  pipelineSteps: [],
  pipelineYaml: "",
  latest: null,
  failures: [],
  volumes: [],
  pagesDone: 0,
  pagesTotal: 0,
  pagesFailed: 0,
  errors: 0,
  lastError: null,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

// Routes every request by the URL it was given: /version (the header), /jobs
// (list) and /jobs/<ns>/<name> (a card's own detail fetch).
function routedFetch(list: unknown, listStatus = 200): typeof fetch {
  return vi.fn(async (url: string) => {
    if (url.toString().endsWith("/version"))
      return jsonResponse({ version: "v0.2.0", web: "0.1.0" });
    if (url.toString().includes("/jobs/")) return jsonResponse(detail);
    return jsonResponse(list, listStatus);
  }) as unknown as typeof fetch;
}

describe("/ campaign page", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  test("fetches GET /api/v1/jobs and renders one card per job", async () => {
    const fetchMock = routedFetch([job]);
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/jobs",
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(screen.getByText("htr-test/kyrk")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("the header links to the source repository", async () => {
    vi.stubGlobal("fetch", routedFetch([job]));
    render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);

    const link = screen.getByRole("link", { name: "htrflow-batch on GitHub" });
    expect(link).toHaveAttribute(
      "href",
      "https://github.com/AI-Riksarkivet/htrflow-batch",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener");
  });

  test("the header names the build answering the page, read once", async () => {
    let versionCalls = 0;
    const fetchMock = vi.fn(async (url: string) => {
      if (url.toString().endsWith("/version")) {
        versionCalls += 1;
        return jsonResponse({ version: "v0.2.0", web: "0.1.0" });
      }
      if (url.toString().includes("/jobs/")) return jsonResponse(detail);
      return jsonResponse([job]);
    }) as unknown as typeof fetch;
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);
    // The deployed tag, not the web package's own version (0.1.0 here).
    expect(screen.getByText("htrflow-batch v0.2.0")).toBeInTheDocument();

    // The build cannot change under a running page: the list polls, this
    // does not.
    await vi.advanceTimersByTimeAsync(RELOAD_MS);
    expect(versionCalls).toBe(1);
  });

  test("no version answer: the header shows no version, and no alert", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.toString().endsWith("/version")
          ? jsonResponse("gone", 503)
          : jsonResponse([]),
      ) as unknown as typeof fetch,
    );
    render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.queryByText(/htrflow-batch/)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("shows an empty state with no campaigns", async () => {
    vi.stubGlobal("fetch", routedFetch([]));
    render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByText("No campaigns.")).toBeInTheDocument();
  });

  test("keeps the last good list through a failed poll and says so in words", async () => {
    // The list endpoint (/jobs, exact) succeeds once then fails; the card's
    // own detail fetch (/jobs/<ns>/<name>) always succeeds, so only the list
    // calls are asserted below.
    let listCalls = 0;
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/api/v1/jobs") {
        listCalls += 1;
        return listCalls === 1
          ? jsonResponse([job])
          : jsonResponse("gone", 503);
      }
      return jsonResponse(detail);
    }) as unknown as typeof fetch;
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByText("htr-test/kyrk")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(RELOAD_MS);
    expect(listCalls).toBe(2);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Can't reach the campaign service right now (HTTP 503). Showing the " +
        "list we last received. Retrying every 60 seconds.",
    );
    expect(screen.getByText("htr-test/kyrk")).toBeInTheDocument();
  });

  // What only this page can be wrong about: the rows it was sent, in the
  // order it was sent them, one card each. How a card draws a running
  // campaign or a reaped one is CampaignCard's own test, and asserting the
  // pulse and the "job removed" chip again here only made the same
  // statement twice (2026-09-14 audit). A campaign whose Job is past its
  // ttlSecondsAfterFinished is served from its two ConfigMaps (B76) and is
  // a row like any other, which is the part that belongs here.
  test("renders the rows the API sent, in that order, live and reaped alike", async () => {
    const reaped = {
      ...job,
      name: "gamla",
      phase: "Succeeded",
      counts: { total: 3, active: 0, done: 3, failed: 0 },
      finishedAt: "2026-09-08T10:00:00Z",
      jobGone: true,
    };
    vi.stubGlobal("fetch", routedFetch([job, reaped]));
    const { container } = render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);

    const names = [...container.querySelectorAll(".camp-name")].map(
      (el) => el.textContent,
    );
    expect(names).toEqual(["htr-test/kyrk", "htr-test/gamla"]);
    expect(container.querySelectorAll("section.campaign")).toHaveLength(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("a malformed 200 body says the versions differ, not 'unreachable'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([{ ...job, phase: "Bogus" }])),
    );
    render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(
      "The campaign service answered in a form this page doesn't understand.",
    );
    expect(alert).not.toHaveTextContent(/unreachable|ZodError/);
  });

  // The list page is left open for hours, with a card per campaign each
  // polling for its own volume table (2026-09-14 audit).
  test("a slow poll is not joined by the next one", async () => {
    let listCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.toString().endsWith("/version"))
          return jsonResponse({ version: "v0.2.0", web: "0.1.0" });
        if (url.toString().includes("/jobs/")) return jsonResponse(detail);
        listCalls += 1;
        return new Promise<Response>(() => {}); // never answers
      }) as unknown as typeof fetch,
    );
    render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(RELOAD_MS * 5);
    expect(listCalls).toBe(1);
  });

  test("nothing is polled while the tab is in the background", async () => {
    const fetchMock = vi.fn(routedFetch([job]));
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);
    const settled = fetchMock.mock.calls.length;

    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(RELOAD_MS * 5);
    expect(fetchMock.mock.calls.length).toBe(settled);

    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => false,
    });
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock.mock.calls.length).toBeGreaterThan(settled);
  });
});

// The API answers newest-declared first, which says nothing about which
// campaign wants a person. The page reads: what is moving, what went wrong,
// what is over (newest first), what has not begun ($lib/order).
describe("/ campaign list order", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function names(rows: unknown[]): Promise<(string | null)[]> {
    vi.stubGlobal("fetch", routedFetch(rows));
    const { container } = render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);
    return [...container.querySelectorAll(".camp-name")].map(
      (el) => el.textContent,
    );
  }

  // The shared `job` fixture is a Running campaign that already carries a
  // failed volume; these are clean but for the phase each is testing.
  const clean = { ...job, counts: { total: 3, active: 0, done: 3, failed: 0 } };
  const queued = { ...clean, name: "queued", phase: "Queued" };
  const broken = { ...clean, name: "broken", phase: "PartiallyFailed" };
  const older = {
    ...clean,
    name: "older",
    phase: "Succeeded",
    finishedAt: "2026-09-01T10:00:00Z",
  };
  const newer = {
    ...clean,
    name: "newer",
    phase: "Succeeded",
    finishedAt: "2026-09-08T10:00:00Z",
  };

  test("running, then broken, then finished newest-first, then waiting", async () => {
    expect(await names([queued, older, newer, broken, job])).toEqual([
      "htr-test/kyrk",
      "htr-test/broken",
      "htr-test/newer",
      "htr-test/older",
      "htr-test/queued",
    ]);
  });

  test("a list of one band keeps the order the API sent", async () => {
    const a = { ...clean, name: "a", phase: "Queued" };
    const b = { ...clean, name: "b", phase: "Paused" };
    expect(await names([b, a])).toEqual(["htr-test/b", "htr-test/a"]);
  });
});
