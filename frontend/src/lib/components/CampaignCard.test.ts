import { fireEvent, render, screen, within } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { JobSummary } from "$lib/api.js";
import CampaignCard from "./CampaignCard.svelte";

const job: JobSummary = {
  namespace: "htr-test",
  name: "kyrk",
  pipeline: "demo-v1",
  phase: "Running",
  counts: { total: 3, active: 1, done: 1, failed: 1 },
  suspended: false,
  createdAt: "2026-01-01T00:00:00Z",
  resultsBase: "https://results.example.org/htr-test/demo-v1",
};

const volumeDone = {
  index: 0,
  id: "vol0",
  state: "done",
  manifestUrl: "https://pub/htr-test/demo-v1/vol0/manifest.json",
  iiifUrl: "https://pub/htr-test/demo-v1/vol0/iiif.json",
  altoPrefix: "https://pub/htr-test/demo-v1/vol0/alto/",
  logUrl: "https://pub/status/logs/demo-v1/vol0.txt",
};

const volumeFailed = {
  index: 1,
  id: "vol1",
  state: "failed",
  manifestUrl: "https://pub/htr-test/demo-v1/vol1/manifest.json",
  iiifUrl: "https://pub/htr-test/demo-v1/vol1/iiif.json",
  altoPrefix: "https://pub/htr-test/demo-v1/vol1/alto/",
  logUrl: "https://pub/status/logs/demo-v1/vol1.txt",
  reason: "permanent failure in load: manifest unsupported",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("CampaignCard", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  test("fetches its own volumes from the read API, paged by index", async () => {
    const detail = {
      ...job,
      failures: [],
      volumes: [volumeDone, volumeFailed],
    };
    const fetchMock = vi.fn(async () => jsonResponse(detail));
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/jobs/htr-test/kyrk?offset=0&limit=200",
      expect.objectContaining({ cache: "no-store" }),
    );
    const rows = screen.getAllByRole("row").slice(1); // drop the header row
    expect(rows).toHaveLength(2);
    expect(
      within(rows[0] as HTMLElement).getByText("vol0"),
    ).toBeInTheDocument();
    expect(
      within(rows[1] as HTMLElement).getByText(/manifest unsupported/),
    ).toBeInTheDocument();
  });

  test("a done volume gets an 'open' link; a non-done volume does not", async () => {
    const detail = {
      ...job,
      failures: [],
      volumes: [volumeDone, volumeFailed],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    const rows = screen.getAllByRole("row").slice(1);
    expect(
      within(rows[0] as HTMLElement).getByRole("link", { name: "open" }),
    ).toHaveAttribute(
      "href",
      "uv.html#?manifest=https://pub/htr-test/demo-v1/vol0/iiif.json",
    );
    expect(
      within(rows[1] as HTMLElement).queryByRole("link", { name: "open" }),
    ).toBeNull();
  });

  test("the log link carries log+manifest always, and live=1 only for a volume that is not done", async () => {
    const detail = {
      ...job,
      failures: [],
      volumes: [volumeDone, volumeFailed],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    const rows = screen.getAllByRole("row").slice(1);
    const doneLog = within(rows[0] as HTMLElement).getByRole("link", {
      name: "log",
    });
    expect(doneLog).toHaveAttribute(
      "href",
      "log?log=" +
        encodeURIComponent(volumeDone.logUrl) +
        "&manifest=" +
        encodeURIComponent(volumeDone.manifestUrl),
    );
    const failedLog = within(rows[1] as HTMLElement).getByRole("link", {
      name: "log",
    });
    expect(failedLog).toHaveAttribute(
      "href",
      "log?log=" +
        encodeURIComponent(volumeFailed.logUrl) +
        "&manifest=" +
        encodeURIComponent(volumeFailed.manifestUrl) +
        "&live=1",
    );
  });

  test("no thumbnails: no <img> anywhere in the card", async () => {
    const detail = { ...job, failures: [], volumes: [volumeDone] };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    expect(container.querySelector("img")).toBeNull();
  });

  test("a 'load more' button appears when more volumes remain, and pages them in", async () => {
    const page1 = { ...job, failures: [], volumes: [volumeDone] };
    const page2 = { ...job, failures: [], volumes: [volumeFailed] };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(page1))
      .mockResolvedValueOnce(jsonResponse(page2));
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignCard, {
      job: { ...job, counts: { ...job.counts, total: 2 } },
    });
    await vi.advanceTimersByTimeAsync(0);

    const more = screen.getByRole("button", { name: /load more/ });
    await fireEvent.click(more);
    await vi.advanceTimersByTimeAsync(0);

    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/v1/jobs/htr-test/kyrk?offset=1&limit=200",
      expect.anything(),
    );
    expect(screen.getAllByRole("row")).toHaveLength(3); // header + 2 volumes
    expect(screen.queryByRole("button", { name: /load more/ })).toBeNull();
  });

  test("an unreachable detail fetch shows an inline error, not a blank table", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse("gone", 503)),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Cannot load volumes: HTTP 503",
    );
  });

  test("the toggle collapses and re-expands the table without refetching", async () => {
    const detail = { ...job, failures: [], volumes: [volumeDone] };
    const fetchMock = vi.fn(async () => jsonResponse(detail));
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    const toggle = screen.getByRole("button", { name: /kyrk$/ });
    expect(screen.getByRole("table")).toBeInTheDocument();
    await fireEvent.click(toggle);
    expect(screen.queryByRole("table")).toBeNull();
    await fireEvent.click(toggle);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1); // still just the initial load
  });

  test("header shows pipeline, phase and counts", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ ...job, failures: [], volumes: [] })),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByText("demo-v1")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText(/1\/3 volumes/)).toBeInTheDocument();
    expect(screen.getByText(/1 failed/)).toBeInTheDocument();
  });
});
