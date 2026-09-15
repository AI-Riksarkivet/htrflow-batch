import { fireEvent, render, screen, within } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { JobSummary } from "$lib/api.js";
import { RELOAD_MS } from "$lib/config.js";
import { describeReason } from "$lib/reasons.js";
import CampaignCard from "./CampaignCard.svelte";

const job: JobSummary = {
  namespace: "htr-test",
  name: "kyrk",
  pipeline: "demo-v1",
  phase: "Running",
  counts: { total: 3, active: 1, done: 1, failed: 1 },
  suspended: false,
  createdAt: "2026-01-01T00:00:00Z",
  finishedAt: null,
  resultsBase: "https://results.example.org/htr-test/demo-v1",
  warmup: { phase: "succeeded" },
  jobGone: false,
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
  progress: null,
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
  progress: null,
};

// Every detail response carries these; a fixture without them would only
// exercise the Zod failure path. `latest` is what the folded strip shows —
// the API computes it over every volume, so null here means "nothing has
// started", not "nothing is loaded".
const detailBase = {
  pipelineSteps: [],
  pipelineYaml: "",
  latest: null,
  pagesDone: 0,
  pagesTotal: 0,
  pagesFailed: 0,
  errors: 0,
  lastError: null,
};
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
      "uv.html#?manifest=" +
        encodeURIComponent("https://pub/htr-test/demo-v1/vol0/iiif.json"),
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
      "uv.html#?manifest=" +
        encodeURIComponent("https://iiif.example.org/vol1/manifest"),
    );

    // no source at all: the open and source slots stay empty, log stays
    const images = within(rows[2] as HTMLElement);
    expect(images.queryByRole("link", { name: "open" })).toBeNull();
    expect(images.queryByRole("link", { name: "source" })).toBeNull();
    expect(images.getByRole("link", { name: "log" })).toBeInTheDocument();
  });

  test("a running volume says how many pages it has done, and of how many", async () => {
    const running = {
      ...volumeDone,
      index: 2,
      id: "vol2",
      state: "active",
      progress: {
        done: 137,
        total: 638,
        failed: 0,
        lastPage: "0137",
        stage: "stream",
        updatedAt: new Date().toISOString(),
        ageSeconds: 12,
        lastError: null,
        errors: 0,
        viewerPublished: true,
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          pagesDone: 137,
          pagesTotal: 638,
          failures: [],
          volumes: [running, volumeDone],
        }),
      ),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    // The campaign's own line is visible folded — that is the question the
    // card is asked most often.
    expect(screen.getByText(/137\s*\/\s*638 pages/)).toBeInTheDocument();
    await expand();

    const row = within(screen.getAllByRole("row").slice(1)[0] as HTMLElement);
    expect(
      row.getByText(/137 \/ 638 pages · processing pages · updated/),
    ).toBeInTheDocument();
    // A volume with nothing to report shows the state chip and no line.
    const done = within(screen.getAllByRole("row").slice(1)[1] as HTMLElement);
    expect(done.queryByText(/pages ·/)).toBeNull();
  });

  test("the viewer opens a volume as soon as it has a page, not only when it is finished", async () => {
    const started = {
      ...volumeFailed,
      state: "active",
      progress: {
        done: 10,
        total: 638,
        failed: 0,
        lastPage: "0010",
        stage: "stream",
        updatedAt: null,
        ageSeconds: null,
        lastError: null,
        errors: 0,
        // The interim publish (every ten pages) has actually happened here.
        viewerPublished: true,
      },
    };
    const untouched = {
      ...volumeFailed,
      index: 3,
      id: "vol3",
      state: "active",
      progress: {
        done: 0,
        total: 638,
        failed: 0,
        lastPage: null,
        stage: "load",
        updatedAt: null,
        ageSeconds: null,
        lastError: null,
        errors: 0,
        viewerPublished: false,
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          failures: [],
          volumes: [started, untouched],
        }),
      ),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    await expand();

    const rows = screen.getAllByRole("row").slice(1);
    // The wrapper rewrites iiif.json every few pages, so it is there to open.
    expect(
      within(rows[0] as HTMLElement).getByRole("link", { name: "open" }),
    ).toHaveAttribute(
      "href",
      "uv.html#?manifest=" +
        encodeURIComponent("https://pub/htr-test/demo-v1/vol1/iiif.json"),
    );
    // Nothing published yet: still the source manifest, as before.
    expect(
      within(rows[1] as HTMLElement).getByRole("link", { name: "open" }),
    ).toHaveAttribute(
      "href",
      "uv.html#?manifest=" +
        encodeURIComponent("https://iiif.example.org/vol1/manifest"),
    );
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

  test("a 404 says the campaign is gone, not that the service is down", async () => {
    // Not the Job's TTL: a reaped campaign is still served from the record
    // its ConfigMaps keep. A 404 means the campaign file left the repo, and
    // a card left open on a screen is the ordinary way to meet that.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse("job not found", 404)),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(
      "This campaign is gone: its campaign file has been removed from the " +
        "campaigns repo.",
    );
    expect(alert).not.toHaveTextContent(/404|not found/);
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
      "uv.html#?manifest=" +
        encodeURIComponent("https://iiif.example.org/vol260/manifest"),
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
        "2 pages are missing from the results (p012, p045); the volume is " +
          "retried automatically and only those pages are redone.",
      ),
    ).toBeInTheDocument();
  });

  test("a failure already visible as a row in the open table is not listed twice", async () => {
    const offPage = { ...volumeFailed, index: 7, id: "vol7" };
    const detail = {
      ...detail0,
      failures: [volumeFailed, offPage],
      volumes: [volumeDone, volumeFailed],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    // Folded: the table is out of sight, so every failure belongs in the callout.
    expect(screen.getByText("failures (2)")).toBeInTheDocument();
    expect(screen.getAllByText("vol1")).toHaveLength(1);

    await expand();
    // Open: vol1 is a row below, with its reason; only vol7 (not on the
    // loaded page) still needs the callout.
    expect(screen.queryByText("failures (2)")).toBeNull();
    expect(
      screen.getByText("failures not shown below (1)"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("vol1")).toHaveLength(1);
    expect(screen.getByText("vol7")).toBeInTheDocument();
  });

  test("no callout at all when every failure is a row in the open table", async () => {
    const detail = {
      ...detail0,
      failures: [volumeFailed],
      volumes: [volumeDone, volumeFailed],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    await expand();
    expect(screen.queryByText(/^failures/)).toBeNull();
    expect(screen.getAllByText("vol1")).toHaveLength(1);
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

  test("the models the pipeline runs are links to their Hugging Face tree", async () => {
    const detail = {
      ...detail0,
      pipelineSteps: ["Segmentation", "TextRecognition"],
      pipelineYaml: `steps:
- step: Segmentation
  settings:
    model_settings:
      model: Riksarkivet/yolov9-regions-1
      revision: 6fb01d2e6b4ff1d0e1e30b5b5c1c1a2b3c4d5e6f
- step: TextRecognition
  settings:
    model_settings:
      model: Riksarkivet/trocr-base-handwritten-hist-swe-2
`,
      failures: [],
      volumes: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    const pinned = screen.getByRole("link", {
      name: "yolov9-regions-1 @6fb01d2",
    });
    expect(pinned).toHaveAttribute(
      "href",
      "https://huggingface.co/Riksarkivet/yolov9-regions-1/tree/" +
        "6fb01d2e6b4ff1d0e1e30b5b5c1c1a2b3c4d5e6f",
    );
    expect(pinned).toHaveAttribute("rel", "noopener");
    expect(pinned).toHaveAttribute("target", "_blank");
    // An unpinned model says so, and its link is the repo's default branch.
    expect(
      screen.getByRole("link", {
        name: "trocr-base-handwritten-hist-swe-2 unpinned",
      }),
    ).toHaveAttribute(
      "href",
      "https://huggingface.co/Riksarkivet/trocr-base-handwritten-hist-swe-2/tree/main",
    );
  });

  test("a pipeline with no models renders no models in the quiet line", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          pipelineYaml: "steps:\n- step: Export\n",
          failures: [],
          volumes: [],
        }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    expect(container.querySelector(".models")).toBeNull();
    // The quiet line stays -- it still carries the dates.
    expect(container.querySelector(".card-meta")).not.toBeNull();
  });

  // The product owner, 2026-09-14, on the live status page: "can we put the
  // create date somewhere else? the layout is a bit bad; also the list of
  // used models is a bit dominant." Both now sit in one small muted line
  // under the header row, which is left reading name -> status -> counts.
  test("the created date and the models share one quiet line below the header", async () => {
    const detail = {
      ...detail0,
      pipelineYaml: `steps:
- step: Segmentation
  settings:
    model_settings:
      model: Riksarkivet/yolov9-regions-1
`,
      failures: [],
      volumes: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    const meta = container.querySelector(".card-meta") as HTMLElement;
    const created = within(meta).getByTitle("2026-01-01T00:00:00Z");
    expect(created.tagName).toBe("TIME");
    expect(created).toHaveAttribute("datetime", "2026-01-01T00:00:00Z");
    // Same line, no chip styling on it, and the link is still a link.
    const link = within(meta).getByRole("link", {
      name: "yolov9-regions-1 unpinned",
    });
    expect(link.className).not.toContain("chip");
    expect(meta.querySelector(".models")).toHaveAttribute(
      "title",
      "Models: yolov9-regions-1 unpinned",
    );
    // ...and neither of them is in the header row any more.
    const header = container.querySelector(".camp") as HTMLElement;
    expect(header.textContent).not.toContain("created");
    expect(header.textContent).not.toContain("Models");
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

  describe("warm-up status chip", () => {
    function stubDetail(j: JobSummary): void {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          jsonResponse({ ...j, ...detailBase, failures: [], volumes: [] }),
        ),
      );
    }

    test("pending", async () => {
      const pending: JobSummary = { ...job, warmup: { phase: "pending" } };
      stubDetail(pending);
      render(CampaignCard, { job: pending });
      await vi.advanceTimersByTimeAsync(0);
      expect(screen.getByText("warm-up pending")).toHaveClass("pending");
    });

    test("running", async () => {
      const running: JobSummary = { ...job, warmup: { phase: "running" } };
      stubDetail(running);
      render(CampaignCard, { job: running });
      await vi.advanceTimersByTimeAsync(0);
      expect(screen.getByText("warm-up running")).toHaveClass("running");
    });

    test("missing: no warm-up Job at all, and the card reads as failed", async () => {
      const missing: JobSummary = { ...job, warmup: { phase: "missing" } };
      stubDetail(missing);
      const { container } = render(CampaignCard, { job: missing });
      await vi.advanceTimersByTimeAsync(0);
      expect(screen.getByText("no warm-up")).toHaveClass("missing");
      expect(container.querySelector(".campaign")).toHaveAttribute(
        "data-health",
        "failed",
      );
    });

    test("missing on an already-succeeded campaign: the chip shows, but health is not failed", async () => {
      const missing: JobSummary = {
        ...job,
        phase: "Succeeded",
        counts: { ...job.counts, failed: 0 },
        warmup: { phase: "missing" },
      };
      stubDetail(missing);
      const { container } = render(CampaignCard, { job: missing });
      await vi.advanceTimersByTimeAsync(0);
      expect(screen.getByText("no warm-up")).toHaveClass("missing");
      expect(container.querySelector(".campaign")).toHaveAttribute(
        "data-health",
        "done",
      );
    });

    test("failed: the chip's tooltip, and a line under it once the card is open", async () => {
      const reason = {
        stage: "warmup",
        permanent: true,
        error: "unknown model class 'Yolo9'",
      };
      const failed: JobSummary = {
        ...job,
        warmup: { phase: "failed", reason },
      };
      stubDetail(failed);
      render(CampaignCard, { job: failed });
      await vi.advanceTimersByTimeAsync(0);
      const chip = screen.getByText("warm-up failed");
      expect(chip).toHaveClass("failed");
      expect(chip).toHaveAttribute("title", describeReason(reason));
      expect(screen.queryByText(describeReason(reason))).toBeNull();
      await expand();
      expect(screen.getByText(describeReason(reason))).toBeInTheDocument();
    });

    test("succeeded: no chip at all", async () => {
      stubDetail(job);
      render(CampaignCard, { job });
      await vi.advanceTimersByTimeAsync(0);
      expect(screen.queryByText(/warm-up/)).toBeNull();
      expect(screen.queryByText("no warm-up")).toBeNull();
    });
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

describe("CampaignCard's failure notice", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    storage = stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const lastError = {
    page: "0044",
    error: "htrflow's Segmentation worker thread died",
    volume: "vol1",
    logUrl: "https://pub/status/logs/demo-v1/vol1.txt",
  };

  function renderWith(notice: Record<string, unknown>) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, ...notice, failures: [], volumes: [] }),
      ),
    );
    render(CampaignCard, { job });
  }

  test("a failed page shows on the folded card, and links to that volume's log", async () => {
    renderWith({ pagesFailed: 1, errors: 2, lastError });
    await vi.advanceTimersByTimeAsync(0);

    const chip = screen.getByRole("link", { name: /1 page failed/ });
    expect(chip).toHaveTextContent(
      "1 page failed · 2 errors · page 0044: htrflow's Segmentation " +
        "worker thread died",
    );
    // The whole sentence stays readable even when the chip clips it.
    expect(chip).toHaveAttribute("title", expect.stringContaining("0044"));
    expect(chip).toHaveAttribute(
      "href",
      "log?log=https%3A%2F%2Fpub%2Fstatus%2Flogs%2Fdemo-v1%2Fvol1.txt&live=1",
    );
  });

  test("errors alone still get a notice, without a log link", async () => {
    renderWith({ pagesFailed: 0, errors: 3, lastError: null });
    await vi.advanceTimersByTimeAsync(0);
    // Two nodes carry the sentence on purpose (finding 7): a visible one
    // clipped by CSS and a visually-hidden one that keeps it reachable by
    // keyboard/assistive tech even when the visible copy is truncated.
    expect(screen.getAllByText("3 errors")).toHaveLength(2);
    expect(screen.queryByRole("link", { name: /error/ })).toBeNull();
  });

  test("the notice's full sentence is reachable without a mouse, not title-only", async () => {
    const lastError = {
      page: "0044",
      error: "htrflow's Segmentation worker thread died",
      volume: "vol1",
      logUrl: "https://pub/status/logs/demo-v1/vol1.txt",
    };
    renderWith({ pagesFailed: 1, errors: 0, lastError });
    await vi.advanceTimersByTimeAsync(0);
    const chip = screen.getByRole("link", { name: /1 page failed/ });
    // The full sentence sits in a `.sr-only` node inside the chip -- not
    // only in its `title`, which a keyboard-only (non-mouse) user never
    // sees.
    const hidden = within(chip).getByText(
      /1 page failed.*Segmentation worker thread died/,
      { selector: ".sr-only" },
    );
    expect(hidden).toBeInTheDocument();
  });

  test("a clean campaign has no notice at all", async () => {
    renderWith({});
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.queryByText(/failed ·|warning/)).toBeNull();
  });
});

// A campaign whose Job is past its ttlSecondsAfterFinished: the two
// ConfigMaps are all that is left, and the card has to say so rather than
// look like a campaign someone can still open a pod of (B76).
describe("a campaign whose Job has been removed", () => {
  const reaped: JobSummary = {
    ...job,
    phase: "Succeeded",
    counts: { total: 3, active: 0, done: 3, failed: 0 },
    finishedAt: "2026-09-08T10:00:00Z",
    jobGone: true,
  };

  test("wears a job removed chip", () => {
    render(CampaignCard, { job: reaped });
    expect(screen.getByText("job removed")).toBeTruthy();
  });

  test("says when it finished", () => {
    render(CampaignCard, { job: reaped });
    const when = screen.getByTitle("2026-09-08T10:00:00Z");
    expect(when.textContent).toBeTruthy();
  });

  test("an outcome nobody recorded reads Unknown, never Running", () => {
    const unknown: JobSummary = { ...reaped, phase: "Unknown" };
    render(CampaignCard, { job: unknown });
    expect(screen.getByText("outcome unknown")).toBeTruthy();
    expect(screen.getByText("job removed")).toBeTruthy();
    expect(screen.queryByText("Running")).toBeNull();
  });

  test("a campaign whose Job is still there wears no such chip", () => {
    render(CampaignCard, { job });
    expect(screen.queryByText("job removed")).toBeNull();
  });
});

// The product owner, 2026-09-14, watching a live run: "is it possible to add
// some form of animation for running things, it's a bit stale and you kind of
// miss it now". Three gestures, and only for what is running — the keyframes
// themselves are not testable, so what is pinned here is the markup that
// carries them: which nodes get the classes, and what assistive tech reads.
describe("CampaignCard's running motion", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    storage = stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const progress = {
    done: 137,
    total: 638,
    failed: 0,
    lastPage: "0137",
    stage: "stream",
    updatedAt: "2026-01-01T00:00:00Z",
    ageSeconds: 12,
    lastError: null,
    errors: 0,
    viewerPublished: true,
  };
  const running = {
    ...volumeDone,
    index: 2,
    id: "vol2",
    state: "active",
    progress,
  };

  /** One detail body per poll, the last one repeating, so a test can move `done`. */
  function stubPolls(...bodies: Record<string, unknown>[]): void {
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        const body = bodies[Math.min(call, bodies.length - 1)];
        call += 1;
        return jsonResponse({ ...detail0, failures: [], volumes: [], ...body });
      }),
    );
  }

  test("only what is running pulses: the phase chip and the active row", async () => {
    stubPolls({ volumes: [running, volumeDone] });
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    // The chip's word is the accessible name; the dot is decoration beside it.
    const dot = container.querySelector(".chip.phase.running .dot");
    expect(dot).toHaveClass("pulse");
    expect(dot).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByText("Running")).toBeInTheDocument();

    await expand();
    const rows = screen.getAllByRole("row").slice(1);
    expect(
      (rows[0] as HTMLElement).querySelector(".status.active .dot"),
    ).toHaveClass("pulse");
    expect(
      (rows[1] as HTMLElement).querySelector(".status.done .dot"),
    ).not.toHaveClass("pulse");
  });

  test("a Running campaign still waiting on its warm-up does not pulse", async () => {
    // `Running` is the projection's fallback phase, so it is what a campaign
    // reads as before its warm-up has finished -- when nothing is running at
    // all. The warm-up chip beside it is the story there, not a beating dot.
    stubPolls({ volumes: [] });
    const { container } = render(CampaignCard, {
      job: { ...job, warmup: { phase: "pending" } },
    });
    await vi.advanceTimersByTimeAsync(0);

    expect(screen.getByText("warm-up pending")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(container.querySelector(".chip.phase .dot")).toBeNull();
  });

  test("an active row carries a bar sized done/total; a finished row none", async () => {
    stubPolls({ volumes: [running, volumeDone] });
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    await expand();

    const bar = screen.getByRole("progressbar", { name: "Pages done in vol2" });
    expect(bar).toHaveAttribute("aria-valuenow", "137");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "638");
    expect(bar.querySelector(".fill")).toHaveStyle({ width: "21.5%" });
    // vol0 is done: a bar that cannot move says nothing, so it has none.
    expect(screen.queryByRole("progressbar", { name: /vol0/ })).toBeNull();
  });

  test("the header sums the campaign's pages into one bar while it runs", async () => {
    stubPolls({ volumes: [running], pagesDone: 137, pagesTotal: 638 });
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    const bar = screen.getByRole("progressbar", {
      name: "Pages done in campaign kyrk",
    });
    expect(bar).toHaveAttribute("aria-valuenow", "137");
    expect(bar).toHaveAttribute("aria-valuemax", "638");
  });

  test("a campaign that is not Running has neither bar nor dot", async () => {
    stubPolls({ volumes: [running], pagesDone: 137, pagesTotal: 638 });
    const { container } = render(CampaignCard, {
      job: { ...job, phase: "Succeeded" },
    });
    await vi.advanceTimersByTimeAsync(0);

    expect(screen.queryByRole("progressbar", { name: /campaign/ })).toBeNull();
    expect(container.querySelector(".chip.phase .dot")).toBeNull();
  });

  test("no header bar while the campaign's page totals are still unknown", async () => {
    stubPolls({ volumes: [running] }); // pagesTotal 0: nothing to be a fraction of
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.queryByRole("progressbar", { name: /campaign/ })).toBeNull();
  });

  test("the progress line flashes only once its page count has moved", async () => {
    const steady = { ...running, index: 3, id: "vol3" };
    stubPolls(
      { volumes: [running, steady] },
      {
        volumes: [{ ...running, progress: { ...progress, done: 151 } }, steady],
      },
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    await expand();
    // First paint is not news: a row nobody has seen before cannot have moved.
    expect(container.querySelectorAll(".vprogress.bump")).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(RELOAD_MS);
    expect(screen.getByText(/151 \/ 638 pages/)).toBeInTheDocument();
    const flashed = container.querySelectorAll(".vprogress.bump");
    expect(flashed).toHaveLength(1); // vol3 sent the same count back
    expect(flashed[0]).toHaveTextContent("151 / 638 pages");
  });
});

describe("a progress bar cannot be talked out of its own scale", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function campaignBar(
    pagesDone: number,
    pagesTotal: number,
  ): Promise<HTMLElement> {
    const body = {
      ...detail0,
      failures: [],
      volumes: [],
      pagesDone,
      pagesTotal,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(body)),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    return container.querySelector('[role="progressbar"]') as HTMLElement;
  }

  // done/total come from the wrapper's progress.json in the results bucket:
  // a document that arrives over the network, and one a half-written run can
  // make disagree with itself (2026-09-14 audit).
  test("more pages done than the volume has fills the bar exactly once", async () => {
    const bar = await campaignBar(900, 100);
    expect(bar.querySelector(".fill")).toHaveStyle({ width: "100%" });
    expect(bar).toHaveAttribute("aria-valuenow", "100");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
  });

  test("a negative count reads as nothing done, not a bar running backwards", async () => {
    const bar = await campaignBar(-5, 100);
    expect(bar.querySelector(".fill")).toHaveStyle({ width: "0%" });
    expect(bar).toHaveAttribute("aria-valuenow", "0");
  });

  test("an ordinary fraction is unchanged", async () => {
    const bar = await campaignBar(25, 100);
    expect(bar.querySelector(".fill")).toHaveStyle({ width: "25%" });
    expect(bar).toHaveAttribute("aria-valuenow", "25");
  });
});

describe("the viewer link is built the way every other link is", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function openLink(volume: unknown): Promise<HTMLElement | null> {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [], volumes: [volume] }),
      ),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    await expand();
    const row = screen.getAllByRole("row").slice(1)[0] as HTMLElement;
    return within(row).queryByRole("link", { name: "open" });
  }

  // iiifUrl is built by the API from a volume id that came off a campaign's
  // volumes.txt, a file people edit in a git repo. `sourceUrl` was checked
  // at this last step and `iiifUrl` was not (2026-09-14 audit).
  test("an iiifUrl that is not an http(s) URL never reaches the viewer", async () => {
    const hostile = { ...volumeDone, iiifUrl: "javascript:alert(1)" };
    expect(await openLink(hostile)).toBeNull();
  });

  test("a manifest URL is encoded into the fragment, not pasted into it", async () => {
    const odd = {
      ...volumeDone,
      iiifUrl: "https://pub/htr-test/demo-v1/vol %261/iiif.json",
    };
    expect(await openLink(odd)).toHaveAttribute(
      "href",
      "uv.html#?manifest=" +
        encodeURIComponent("https://pub/htr-test/demo-v1/vol %261/iiif.json"),
    );
  });

  test("an ordinary manifest URL still opens", async () => {
    expect(await openLink(volumeDone)).toHaveAttribute(
      "href",
      "uv.html#?manifest=" +
        encodeURIComponent("https://pub/htr-test/demo-v1/vol0/iiif.json"),
    );
  });
});
