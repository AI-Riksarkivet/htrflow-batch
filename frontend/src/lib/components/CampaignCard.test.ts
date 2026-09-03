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
  sourceUrl: "https://iiif.example.org/vol0/manifest",
};

const volumeFailed = {
  index: 1,
  id: "vol1",
  state: "failed",
  manifestUrl: "https://pub/htr-test/demo-v1/vol1/manifest.json",
  iiifUrl: "https://pub/htr-test/demo-v1/vol1/iiif.json",
  altoPrefix: "https://pub/htr-test/demo-v1/vol1/alto/",
  logUrl: "https://pub/status/logs/demo-v1/vol1.txt",
  sourceUrl: "https://iiif.example.org/vol1/manifest",
  reason: "permanent failure in load: manifest unsupported",
};

// Every detail response carries the pipeline fields; a fixture without them
// would only exercise the Zod failure path.
const pipelineless = { pipelineSteps: [], pipelineYaml: "" };
const detail0 = { ...job, ...pipelineless };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Cards are folded by default; most tests want the volume table open. */
async function expand(): Promise<void> {
  await fireEvent.click(screen.getByRole("button", { name: /kyrk$/ }));
}

/**
 * jsdom here ships no localStorage at all, which is one of the cases the
 * card's try/catch is for — so the tests that care about the remembered fold
 * state stub one in, and every other test exercises the no-storage default.
 */
function stubStorage(): Map<string, string> {
  const map = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
  });
  return map;
}

describe("CampaignCard", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  test("fetches its own volumes from the read API, paged by index", async () => {
    const detail = {
      ...detail0,
      failures: [],
      volumes: [volumeDone, volumeFailed],
    };
    const fetchMock = vi.fn(async () => jsonResponse(detail));
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    await expand();

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

  test("every row has the three slots: open, source and log", async () => {
    const imagesVolume = {
      ...volumeFailed,
      index: 2,
      id: "vol2",
      state: "pending",
      sourceUrl: null, // an `images:` volume has no manifest to open
    };
    const detail = {
      ...detail0,
      failures: [],
      volumes: [volumeDone, volumeFailed, imagesVolume],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    await expand();

    const rows = screen.getAllByRole("row").slice(1);
    const done = within(rows[0] as HTMLElement);
    // done: the published result
    expect(done.getByRole("link", { name: "open" })).toHaveAttribute(
      "href",
      "uv.html#?manifest=https://pub/htr-test/demo-v1/vol0/iiif.json",
    );
    expect(done.getByRole("link", { name: "source" })).toHaveAttribute(
      "href",
      "https://iiif.example.org/vol0/manifest",
    );
    expect(done.getByRole("link", { name: "log" })).toBeInTheDocument();

    // not done, but it has a source: "open" shows the source manifest
    expect(
      within(rows[1] as HTMLElement).getByRole("link", { name: "open" }),
    ).toHaveAttribute(
      "href",
      "uv.html#?manifest=https://iiif.example.org/vol1/manifest",
    );

    // no source at all: the open and source slots stay empty, log stays
    const images = within(rows[2] as HTMLElement);
    expect(images.queryByRole("link", { name: "open" })).toBeNull();
    expect(images.queryByRole("link", { name: "source" })).toBeNull();
    expect(images.getByRole("link", { name: "log" })).toBeInTheDocument();
  });

  test("the log link carries log+manifest always, and live=1 only for a volume that is not done", async () => {
    const detail = {
      ...detail0,
      failures: [],
      volumes: [volumeDone, volumeFailed],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    await expand();

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
    const detail = { ...detail0, failures: [], volumes: [volumeDone] };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    expect(container.querySelector("img")).toBeNull();
  });

  test("a 'load more' button appears when more volumes remain, and pages them in", async () => {
    const page1 = { ...detail0, failures: [], volumes: [volumeDone] };
    const page2 = { ...detail0, failures: [], volumes: [volumeFailed] };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(page1))
      .mockResolvedValueOnce(jsonResponse(page2));
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignCard, {
      job: { ...job, counts: { ...job.counts, total: 2 } },
    });
    await vi.advanceTimersByTimeAsync(0);
    await expand();

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

  test("folded by default; the toggle opens and closes without refetching", async () => {
    const detail = { ...detail0, failures: [], volumes: [volumeDone] };
    const fetchMock = vi.fn(async () => jsonResponse(detail));
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    const toggle = screen.getByRole("button", { name: /kyrk$/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("table")).toBeNull();
    await fireEvent.click(toggle);
    expect(screen.getByRole("table")).toBeInTheDocument();
    await fireEvent.click(toggle);
    expect(screen.queryByRole("table")).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1); // still just the initial load
  });

  test("the fold state is remembered per campaign", async () => {
    const store = stubStorage();
    const detail = { ...detail0, failures: [], volumes: [volumeDone] };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    const first = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    await expand();
    expect(store.get("htrflow.card.htr-test/kyrk")).toBe("open");
    first.unmount();

    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByRole("table")).toBeInTheDocument(); // opens as left
  });

  test("a card whose storage throws still renders, folded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [], volumes: [volumeDone] }),
      ),
    );
    const boom = () => {
      throw new Error("storage disabled"); // cookies blocked, private mode
    };
    vi.stubGlobal("localStorage", { getItem: boom, setItem: boom });
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.queryByRole("table")).toBeNull();
    await expand();
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  test("while folded, the newest volume keeps open · source · log in reach", async () => {
    const active = {
      ...volumeDone,
      index: 2,
      id: "vol2",
      state: "active",
      sourceUrl: "https://iiif.example.org/vol2/manifest",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          failures: [],
          volumes: [volumeDone, active],
        }),
      ),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    expect(screen.queryByRole("table")).toBeNull(); // still folded
    expect(screen.getByText("vol2")).toBeInTheDocument(); // the active one wins
    expect(screen.getByRole("link", { name: "open" })).toHaveAttribute(
      "href",
      "uv.html#?manifest=https://iiif.example.org/vol2/manifest",
    );
    expect(screen.getByRole("link", { name: "source" })).toHaveAttribute(
      "href",
      "https://iiif.example.org/vol2/manifest",
    );
    expect(screen.getByRole("link", { name: "log" })).toHaveAttribute(
      "href",
      expect.stringContaining(encodeURIComponent(active.logUrl)),
    );
  });

  test("no volume in flight: the folded strip shows the newest done one", async () => {
    const olderDone = { ...volumeDone, index: 0, id: "vol0" };
    const newerDone = { ...volumeDone, index: 1, id: "vol1" };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          failures: [],
          volumes: [
            olderDone,
            newerDone,
            { ...volumeFailed, index: 2, id: "vol2" },
          ],
        }),
      ),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByText("vol1")).toBeInTheDocument();
    expect(screen.queryByText("vol0")).toBeNull();
  });

  test("renders the failures block with both ids and reasons", async () => {
    const secondFailure = {
      ...volumeFailed,
      index: 2,
      id: "vol2",
      reason: "transient failure in fetch: connection reset",
    };
    const detail = {
      ...detail0,
      failures: [volumeFailed, secondFailure],
      volumes: [volumeDone],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    expect(screen.getByText("failures (2)")).toBeInTheDocument();
    expect(screen.getByText("vol1")).toBeInTheDocument();
    expect(
      screen.getByText("permanent failure in load: manifest unsupported"),
    ).toBeInTheDocument();
    expect(screen.getByText("vol2")).toBeInTheDocument();
    expect(
      screen.getByText("transient failure in fetch: connection reset"),
    ).toBeInTheDocument();
  });

  test("the pipeline chip lists its steps and toggles the YAML", async () => {
    const detail = {
      ...detail0,
      pipelineSteps: ["Segmentation", "TextRecognition"],
      pipelineYaml: "steps:\n- step: Segmentation\n",
      failures: [],
      volumes: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    const chip = screen.getByRole("button", { name: "demo-v1" });
    expect(chip).toHaveAttribute("title", "Segmentation → TextRecognition");
    expect(chip).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/- step: Segmentation/)).toBeNull();

    await fireEvent.click(chip);
    expect(chip).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/- step: Segmentation/)).toBeInTheDocument();
  });

  test("no pipeline YAML: the chip is a static label, not a button", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [], volumes: [] }),
      ),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.queryByRole("button", { name: "demo-v1" })).toBeNull();
    expect(screen.getByText("demo-v1")).toBeInTheDocument();
  });

  test("a partially failed campaign says so in words, in the warning colour", async () => {
    const partly: JobSummary = { ...job, phase: "PartiallyFailed" };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...partly, ...pipelineless, failures: [], volumes: [] }),
      ),
    );
    render(CampaignCard, { job: partly });
    await vi.advanceTimersByTimeAsync(0);
    const chip = screen.getByText("partially failed");
    expect(chip).toHaveClass("partiallyfailed"); // warning, not destructive
  });

  test("header shows pipeline, phase and counts", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [], volumes: [] }),
      ),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByText("demo-v1")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText(/1\/3 volumes/)).toBeInTheDocument();
    expect(screen.getByText(/1 failed/)).toBeInTheDocument();
  });
});
