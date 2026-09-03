import { fireEvent, render, screen, within } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { JobSummary } from "$lib/api.js";
import { RELOAD_MS } from "$lib/config.js";
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
  reason: { stage: "load", permanent: true, error: "model not found" },
};

// Every detail response carries these; a fixture without them would only
// exercise the Zod failure path. `latest` is what the folded strip shows —
// the API computes it over every volume, so null here means "nothing has
// started", not "nothing is loaded".
const detailBase = { pipelineSteps: [], pipelineYaml: "", latest: null };
const detail0 = { ...job, ...detailBase };

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
 * The card remembers its fold state in localStorage, and the environments
 * these tests run in disagree about whether there is one (CI's jsdom has it,
 * the local one does not). Stub a fresh store per test so neither the
 * environment nor the previous test can decide whether a card starts folded.
 */
let storage: Map<string, string>;

function stubStorage(): Map<string, string> {
  const map = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
  });
  return map;
}

describe("CampaignCard", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    storage = stubStorage();
  });
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
      within(rows[1] as HTMLElement).getByText(/model not found/),
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

  test("a sourceUrl that is not an http(s) URL never becomes a link", async () => {
    // volumes.txt is a file humans edit in a git repo; the card checks the
    // URL again at the last step before it becomes an href.
    const hostile = {
      ...volumeFailed,
      sourceUrl: "javascript:alert(1)",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [], volumes: [hostile] }),
      ),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    await expand();

    const row = screen.getAllByRole("row").slice(1)[0] as HTMLElement;
    expect(within(row).queryByRole("link", { name: "source" })).toBeNull();
    // and it must not reach the viewer through the "open" slot either
    expect(within(row).queryByRole("link", { name: "open" })).toBeNull();
    expect(within(row).getByRole("link", { name: "log" })).toBeInTheDocument();
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

  test("a poll keeps every page that has been loaded", async () => {
    // PAGE is 200; two pages loaded means the poll must ask for 400 from 0.
    const page = (from: number, n: number) =>
      Array.from({ length: n }, (_, i) => ({
        ...volumeDone,
        index: from + i,
        id: `vol${from + i}`,
      }));
    const first = page(0, 200);
    const second = page(200, 50);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ ...detail0, failures: [], volumes: first }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ ...detail0, failures: [], volumes: second }),
      )
      .mockResolvedValue(
        jsonResponse({
          ...detail0,
          failures: [],
          volumes: [...first, ...second],
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignCard, {
      job: { ...job, counts: { ...job.counts, total: 250 } },
    });
    await vi.advanceTimersByTimeAsync(0);
    await expand();
    await fireEvent.click(screen.getByRole("button", { name: /load more/ }));
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getAllByRole("row")).toHaveLength(251); // header + 250

    await vi.advanceTimersByTimeAsync(RELOAD_MS); // the poll tick
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/v1/jobs/htr-test/kyrk?offset=0&limit=400",
      expect.anything(),
    );
    expect(screen.getAllByRole("row")).toHaveLength(251); // still both pages
  });

  test("an unreachable detail fetch shows an inline error, not a blank table", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse("gone", 503)),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    // A sentence, not the transport detail: no bare "HTTP 503" line.
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(
      "Can't reach the campaign service right now (HTTP 503).",
    );
    expect(alert).toHaveTextContent("Retrying every 60 seconds.");
  });

  test("folded by default; the toggle opens and closes without refetching", async () => {
    const detail = { ...detail0, failures: [], volumes: [volumeDone] };
    const fetchMock = vi.fn(async () => jsonResponse(detail));
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    const toggle = screen.getByRole("button", { name: /kyrk$/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    // No dangling IDREF while there is no table to point at.
    expect(toggle).not.toHaveAttribute("aria-controls");
    expect(screen.queryByRole("table")).toBeNull();
    await fireEvent.click(toggle);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(
      document.getElementById(toggle.getAttribute("aria-controls") ?? ""),
    ).not.toBeNull();
    await fireEvent.click(toggle);
    expect(screen.queryByRole("table")).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1); // still just the initial load
  });

  test("the fold state is remembered per campaign", async () => {
    const detail = { ...detail0, failures: [], volumes: [volumeDone] };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    const first = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    await expand();
    expect(storage.get("htrflow.card.htr-test/kyrk")).toBe("open");
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

  test("while folded, the API's latest volume keeps open · source · log in reach", async () => {
    // Deliberately NOT among `volumes`: the strip comes from the API's
    // `latest`, computed over every volume, not from the page the card
    // happens to have loaded — which for a big campaign never holds the
    // index in flight.
    const active = {
      ...volumeDone,
      index: 260,
      id: "vol260",
      state: "active",
      sourceUrl: "https://iiif.example.org/vol260/manifest",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          latest: active,
          failures: [],
          volumes: [volumeDone],
        }),
      ),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    expect(screen.queryByRole("table")).toBeNull(); // still folded
    expect(screen.getByText("vol260")).toBeInTheDocument();
    expect(screen.queryByText("vol0")).toBeNull(); // not the loaded row
    expect(screen.getByRole("link", { name: "open" })).toHaveAttribute(
      "href",
      "uv.html#?manifest=https://iiif.example.org/vol260/manifest",
    );
    expect(screen.getByRole("link", { name: "source" })).toHaveAttribute(
      "href",
      "https://iiif.example.org/vol260/manifest",
    );
    expect(screen.getByRole("link", { name: "log" })).toHaveAttribute(
      "href",
      expect.stringContaining(encodeURIComponent(active.logUrl)),
    );
  });

  test("no latest volume: the folded card shows no strip", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          latest: null,
          failures: [],
          volumes: [volumeDone],
        }),
      ),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.queryByRole("link", { name: "log" })).toBeNull();
  });

  test("renders the failures block with both ids and reasons", async () => {
    const secondFailure = {
      ...volumeFailed,
      index: 2,
      id: "vol2",
      reason: {
        stage: "stream",
        permanent: false,
        error: "verify failed: 2 missing, 0 failed missing=['p012', 'p045']",
      },
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
    // Sentences, not the wrapper's fields: no reader ever sees a stage
    // name, a `permanent` flag or a Python list repr.
    expect(
      screen.getByText(
        "Failed while loading the model: model not found. This volume will " +
          "not be retried — fix the cause, then put the volume in a new " +
          "campaign.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("vol2")).toBeInTheDocument();
    expect(
      screen.getByText(
        "2 pages could not be processed (p012, p045); the volume is retried " +
          "automatically and only those pages are redone.",
      ),
    ).toBeInTheDocument();
  });

  test("a raw termination message renders as a sentence, never as JSON", async () => {
    const raw = {
      ...volumeFailed,
      reason: {
        stage: null,
        permanent: null,
        error: '{"stage": "setup", "permanent": true}',
      },
    };
    const detail = { ...detail0, failures: [raw], volumes: [] };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    const block = screen.getByText("failures (1)").closest("div");
    expect(block?.textContent).toContain(
      "The pod stopped without a message this page can read; open the run " +
        "log to see what happened.",
    );
    expect(block?.textContent).not.toContain("permanent");
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
        jsonResponse({ ...partly, ...detailBase, failures: [], volumes: [] }),
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
