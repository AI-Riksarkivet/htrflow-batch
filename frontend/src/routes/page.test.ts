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
  resultsBase: "https://results.example.org/htr-test/demo-v1",
  warmup: { phase: "succeeded" },
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
});
