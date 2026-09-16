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
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    // The campaign's own pages cell is visible folded — that is the question
    // the card is asked most often.
    const numbers = within(container.querySelector(".numbers") as HTMLElement);
    expect(numbers.getByText("pages")).toBeInTheDocument();
    expect(numbers.getByText(/137 \/ 638/)).toBeInTheDocument();
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

  test("a sourceUrl that is not an http(s) URL never reaches the card", async () => {
    // volumes.txt is a file humans edit in a git repo. Since the audit the
    // schema refuses the row at the boundary ($lib/api, httpUrlSchema), so
    // the card never sees it and says what it says about any answer it
    // cannot read; the card's own checks stay as the last step.
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

    expect(screen.queryByRole("link", { name: "source" })).toBeNull();
    expect(screen.queryByRole("link", { name: "open" })).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "answered in a form this page doesn't understand",
    );
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

  test("an unknown row's log link is not live: nothing is writing it", async () => {
    const detail = {
      ...detail0,
      failures: [],
      volumes: [{ ...volumeDone, state: "unknown" }],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    await expand();
    const log = screen.getByRole("link", { name: /log/ });
    expect(log.getAttribute("href")).not.toContain("live=1");
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

  test("zone 3 names every failed volume and why, in one line", async () => {
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
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    // Zone 3: one line, each failure as `id: sentence`. Sentences, not the
    // wrapper's fields — no reader ever sees a stage name, a `permanent`
    // flag or a Python list repr.
    const line = container.querySelector(".problems-text") as HTMLElement;
    expect(line).toHaveTextContent(
      "vol1: Failed while loading the model: model not found. This volume " +
        "will not be retried — fix the cause, then put the volume in a new " +
        "campaign. · vol2: 2 pages are missing from the results (p012, " +
        "p045); the volume is retried automatically and only those pages " +
        "are redone.",
    );
    // Clipped by CSS, so the whole of it has to be reachable two other ways.
    expect(line).toHaveAttribute("title", line.textContent);
    expect(container.querySelector(".problems .sr-only")).toHaveTextContent(
      "vol1: Failed while loading the model",
    );
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
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    const line = () => container.querySelector(".problems-text") as HTMLElement;
    // Folded: the table is out of sight, so every failure belongs on the line.
    expect(line()).toHaveTextContent("vol1:");
    expect(line()).toHaveTextContent("vol7:");

    await expand();
    // Open: vol1 is a row below, with its reason; only vol7 (not on the
    // loaded page) is still worth saying up here.
    expect(line()).not.toHaveTextContent("vol1:");
    expect(line()).toHaveTextContent("vol7:");
  });

  test("no problems line when every failure is a row in the open table", async () => {
    const detail = {
      ...detail0,
      failures: [volumeFailed],
      volumes: [volumeDone, volumeFailed],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    await expand();
    expect(container.querySelector(".problems")).toBeNull();
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
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    const line = container.querySelector(".problems-text") as HTMLElement;
    expect(line).toHaveTextContent(
      "The pod stopped without a message this page can read; open the run " +
        "log to see what happened.",
    );
    expect(line.textContent).not.toContain("permanent");
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
    // Zone 4 is the models and nothing else now, so a pipeline with none
    // has no fourth line at all.
    expect(container.querySelector(".models")).toBeNull();
    expect(container.querySelector(".card-meta")).toBeNull();
  });

  // The product owner, 2026-09-14: "can we put the create date somewhere
  // else? the layout is a bit bad; also the list of used models is a bit
  // dominant." The dates moved again on 2026-09-16, to the right end of the
  // identity line as a range; the models are zone 4, the quietest line.
  test("the run's two ends sit in zone 1; the models are zone 4", async () => {
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

    // Zone 1's right end: the two ends of the run, still machine-readable.
    const when = container.querySelector(".when") as HTMLElement;
    const created = within(when).getByTitle("2026-01-01T00:00:00Z");
    expect(created.tagName).toBe("TIME");
    expect(created).toHaveAttribute("datetime", "2026-01-01T00:00:00Z");
    expect(when).toHaveTextContent("→");
    // Zone 4: the models, no chip styling, the link still a link.
    const meta = container.querySelector(".card-meta") as HTMLElement;
    const link = within(meta).getByRole("link", {
      name: "yolov9-regions-1 unpinned",
    });
    expect(link.className).not.toContain("chip");
    expect(meta.querySelector(".models")).toHaveAttribute(
      "title",
      "Models: yolov9-regions-1 unpinned",
    );
    // The arrow says it visually; the word is there only for a screen
    // reader (2026-09-16 review).
    expect(when.querySelector(".sr-only")).toHaveTextContent("created");
    // The models never sit in the identity line.
    const ident = container.querySelector(".camp") as HTMLElement;
    expect(ident.textContent).not.toContain("Models");
  });

  // The arrow says "and then it finished". A campaign that has not started
  // has nothing on the other side of it to point at, and pointing anyway
  // read as though it were running (2026-09-16 review).
  test.each([["Queued" as const], ["Paused" as const], ["Unknown" as const]])(
    "a %s campaign shows its created date and no arrow",
    async (phase) => {
      const waiting: JobSummary = {
        ...job,
        phase,
        jobGone: phase === "Unknown",
        counts: { total: 3, active: 0, done: 0, failed: 0 },
      };
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          jsonResponse({ ...detail0, ...waiting, failures: [], volumes: [] }),
        ),
      );
      const { container } = render(CampaignCard, { job: waiting });
      await vi.advanceTimersByTimeAsync(0);
      const when = container.querySelector(".when") as HTMLElement;
      expect(
        within(when).getByTitle("2026-01-01T00:00:00Z"),
      ).toBeInTheDocument();
      expect(when.textContent).not.toContain("→");
      expect(when.textContent).not.toContain("…");
    },
  );

  test("a screen reader hears which date is which", async () => {
    const done: JobSummary = {
      ...job,
      phase: "Succeeded",
      finishedAt: "2026-01-01T11:20:00Z",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, ...done, failures: [], volumes: [] }),
      ),
    );
    const { container } = render(CampaignCard, { job: done });
    await vi.advanceTimersByTimeAsync(0);
    const when = container.querySelector(".when") as HTMLElement;
    // The arrow is decoration; the words are what joins the two times.
    expect(when.querySelector(".arrow")).toHaveAttribute("aria-hidden", "true");
    expect(when.textContent).toContain("created");
    expect(when.textContent).toContain("finished");
    expect(when.querySelectorAll(".sr-only")).toHaveLength(2);
  });

  test("a campaign still going shows an open-ended range", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [], volumes: [] }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    const when = container.querySelector(".when") as HTMLElement;
    expect(when).toHaveTextContent("…");
    expect(within(when).getByText(/still running/)).toHaveClass("sr-only");
  });

  test("a run that finished the same day gives its end the clock only", async () => {
    const sameDay = {
      ...job,
      createdAt: "2026-01-01T10:56:00Z",
      finishedAt: "2026-01-01T11:20:00Z",
      phase: "Succeeded" as const,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, ...sameDay, failures: [], volumes: [] }),
      ),
    );
    const { container } = render(CampaignCard, { job: sameDay });
    await vi.advanceTimersByTimeAsync(0);
    const when = container.querySelector(".when") as HTMLElement;
    const end = within(when).getByTitle("2026-01-01T11:20:00Z");
    // "1 Jan, 10:56 → 11:20": the date is said once.
    expect(end.textContent).toMatch(/^\d{2}:\d{2}$/);
    expect(within(when).getByTitle("2026-01-01T10:56:00Z")).toHaveTextContent(
      "Jan",
    );
  });

  test("a run spanning days says both dates", async () => {
    const overnight = {
      ...job,
      createdAt: "2026-01-01T12:00:00Z",
      finishedAt: "2026-01-03T12:00:00Z",
      phase: "Succeeded" as const,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, ...overnight, failures: [], volumes: [] }),
      ),
    );
    const { container } = render(CampaignCard, { job: overnight });
    await vi.advanceTimersByTimeAsync(0);
    const when = container.querySelector(".when") as HTMLElement;
    expect(within(when).getByTitle("2026-01-03T12:00:00Z")).toHaveTextContent(
      "Jan",
    );
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
      // The sentence is written out, not computed by calling the renderer
      // this card calls: an expectation built from the code under test
      // passes whatever that code says (2026-09-14 audit). $lib/reasons has
      // its own tests for how the sentence is built.
      const sentence =
        "The warm-up failed: unknown model class 'Yolo9'. Fix the pipeline " +
        "file, then re-apply it — the warm-up will not retry on its own.";
      expect(describeReason(reason)).toBe(sentence);
      const chip = screen.getByText("warm-up failed");
      expect(chip).toHaveClass("failed");
      expect(chip).toHaveAttribute("title", sentence);
      // Zone 3 carries the sentence itself, folded or open: a warm-up that
      // failed is the reason nothing is happening, and the card should not
      // make a reader open it to find that out (2026-09-16).
      const line = document.querySelector(".problems-text") as HTMLElement;
      expect(line).toHaveTextContent(`warm-up: ${sentence}`);
    });

    test("succeeded: no chip at all", async () => {
      stubDetail(job);
      render(CampaignCard, { job });
      await vi.advanceTimersByTimeAsync(0);
      expect(screen.queryByText(/warm-up/)).toBeNull();
      expect(screen.queryByText("no warm-up")).toBeNull();
    });
  });

  test("zone 1 identifies the campaign; zone 2 counts it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [], volumes: [] }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    const ident = container.querySelector(".camp") as HTMLElement;
    expect(within(ident).getByText("demo-v1")).toBeInTheDocument();
    expect(within(ident).getByText("Running")).toBeInTheDocument();
    expect(within(ident).getByText("htr-test/kyrk")).toBeInTheDocument();

    const numbers = container.querySelector(".numbers") as HTMLElement;
    expect(within(numbers).getByText("volumes")).toBeInTheDocument();
    expect(within(numbers).getByText(/1 \/ 3/)).toBeInTheDocument();
    expect(within(numbers).getByText(/1 failed/)).toBeInTheDocument();
    // The counts never sit in the identity line any more.
    expect(within(ident).queryByText(/1 \/ 3/)).toBeNull();
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

  test("the last page error shows folded, with a link to that volume's log", async () => {
    renderWith({ pagesFailed: 1, errors: 2, lastError });
    await vi.advanceTimersByTimeAsync(0);

    const line = document.querySelector(".problems-text") as HTMLElement;
    expect(line).toHaveTextContent(
      "page 0044: htrflow's Segmentation worker thread died",
    );
    // The counts are zone 2's job now and are not said twice.
    expect(line.textContent).not.toContain("1 page failed");
    expect(line.textContent).not.toContain("2 errors");
    expect(line).toHaveAttribute("title", line.textContent);
    expect(screen.getByRole("link", { name: "log" })).toHaveAttribute(
      "href",
      "log?log=https%3A%2F%2Fpub%2Fstatus%2Flogs%2Fdemo-v1%2Fvol1.txt&live=1",
    );
  });

  test("errors with no sentence behind them are a number, not a line", async () => {
    renderWith({ pagesFailed: 0, errors: 3, lastError: null });
    await vi.advanceTimersByTimeAsync(0);
    // Zone 2 counts them; zone 3 has nothing to say that zone 2 has not.
    const errors = document.querySelector(".metric.errors") as HTMLElement;
    expect(errors).toHaveTextContent("errors");
    expect(errors).toHaveTextContent("3");
    expect(document.querySelector(".problems")).toBeNull();
  });

  test("the problems line is reachable without a mouse, not title-only", async () => {
    renderWith({ pagesFailed: 1, errors: 0, lastError });
    await vi.advanceTimersByTimeAsync(0);
    // The full sentence sits in a `.sr-only` node beside the clipped one --
    // not only in its `title`, which a keyboard-only user never sees.
    const hidden = document.querySelector(".problems .sr-only") as HTMLElement;
    expect(hidden).toHaveTextContent(
      "page 0044: htrflow's Segmentation worker thread died",
    );
  });

  test("a clean campaign has no problems line at all", async () => {
    renderWith({});
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector(".problems")).toBeNull();
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

  // `getByText` already throws when there is no such element, so
  // `.toBeTruthy()` on its result asserted nothing at all (2026-09-14
  // audit). What each of these is really about is which element it is and
  // what it says.
  test("wears a job removed chip", () => {
    render(CampaignCard, { job: reaped });
    const chip = screen.getByText("job removed");
    expect(chip).toHaveClass("chip", "gone");
    expect(chip).toHaveAttribute("title", expect.stringContaining("removed"));
  });

  test("says when it finished, in words and in the machine-readable form", () => {
    render(CampaignCard, { job: reaped });
    const when = screen.getByTitle("2026-09-08T10:00:00Z");
    expect(when.tagName).toBe("TIME");
    expect(when).toHaveAttribute("datetime", "2026-09-08T10:00:00Z");
    expect(when).toHaveTextContent(/\d/);
  });

  test("an outcome nobody recorded reads Unknown, never Running", () => {
    const unknown: JobSummary = { ...reaped, phase: "Unknown" };
    const { container } = render(CampaignCard, { job: unknown });
    expect(screen.getByText("outcome unknown")).toHaveClass("chip", "phase");
    expect(screen.getByText("job removed")).toHaveClass("chip", "gone");
    expect(screen.queryByText("Running")).toBeNull();
    // Styled like a campaign that is over, not like one that went wrong.
    expect(container.querySelector(".campaign")).toHaveAttribute(
      "data-health",
      "idle",
    );
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

  test("zone 2 sums the campaign's pages into a bar that sheens while it runs", async () => {
    stubPolls({ volumes: [running], pagesDone: 137, pagesTotal: 638 });
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    const bar = screen.getByRole("progressbar", {
      name: "pages done in campaign kyrk",
    });
    expect(bar).toHaveAttribute("aria-valuenow", "137");
    expect(bar).toHaveAttribute("aria-valuemax", "638");
    expect(bar.querySelector(".fill")).toHaveClass("running");
  });

  test("a campaign that is not Running keeps its bars but they do not move", async () => {
    stubPolls({ volumes: [running], pagesDone: 137, pagesTotal: 638 });
    const { container } = render(CampaignCard, {
      job: { ...job, phase: "Succeeded", counts: { ...job.counts, failed: 0 } },
    });
    await vi.advanceTimersByTimeAsync(0);

    // Every card carries the same two bars; only a running one sheens.
    const bar = screen.getByRole("progressbar", {
      name: "pages done in campaign kyrk",
    });
    expect(bar.querySelector(".fill")).not.toHaveClass("running");
    expect(container.querySelector(".chip.phase .dot")).toBeNull();
  });

  test("a total nobody knows yet is an em dash, not a bar of nothing", async () => {
    stubPolls({ volumes: [running] }); // pagesTotal 0: nothing to be a fraction of
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    expect(
      screen.queryByRole("progressbar", { name: /pages done in campaign/ }),
    ).toBeNull();
    const cells = [...container.querySelectorAll(".metric")];
    const pagesCell = cells.find((c) => c.textContent?.startsWith("pages"));
    expect(pagesCell).toHaveTextContent("—");
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
    return container.querySelector(
      '[role="progressbar"][aria-label^="pages"]',
    ) as HTMLElement;
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
  // at this last step and `iiifUrl` was not (2026-09-14 audit); the schema
  // now refuses such a row outright, and this is the belt beside it.
  test("an iiifUrl that is not an http(s) URL never reaches the viewer", async () => {
    const hostile = { ...volumeDone, iiifUrl: "javascript:alert(1)" };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [], volumes: [hostile] }),
      ),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.queryByRole("link", { name: "open" })).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "answered in a form this page doesn't understand",
    );
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

// R1: the Job is gone, but manifest.json, iiif.json, alto/ and the run log
// are all still in the bucket. The card has to draw those rows like any
// other — the whole point of rebuilding them server-side was that a
// finished campaign stays openable (the product owner, 2026-09-14).
describe("a reaped campaign's volumes are still openable", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const reaped: JobSummary = {
    ...job,
    phase: "Succeeded",
    counts: { total: 3, active: 0, done: 2, failed: 1 },
    finishedAt: "2026-09-08T10:00:00Z",
    jobGone: true,
  };

  const failedRow = {
    ...volumeFailed,
    reason: { stage: null, permanent: null, error: "manifest 404" },
  };

  async function openCard(rows: unknown[], row: JobSummary = reaped) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          ...row,
          failures: rows.filter(
            (v) => (v as { state: string }).state === "failed",
          ),
          volumes: rows,
        }),
      ),
    );
    render(CampaignCard, { job: row });
    await vi.advanceTimersByTimeAsync(0);
    await expand();
    return screen.getAllByRole("row").slice(1) as HTMLElement[];
  }

  test("every row is there, with its open, source and log links", async () => {
    const rows = await openCard([volumeDone, failedRow]);
    expect(rows).toHaveLength(2);
    const done = within(rows[0] as HTMLElement);
    expect(done.getByRole("link", { name: "open" })).toHaveAttribute(
      "href",
      "uv.html#?manifest=" + encodeURIComponent(volumeDone.iiifUrl),
    );
    expect(done.getByRole("link", { name: "source" })).toHaveAttribute(
      "href",
      volumeDone.sourceUrl,
    );
    expect(done.getByRole("link", { name: "log" })).toHaveAttribute(
      "href",
      expect.stringContaining(encodeURIComponent(volumeDone.logUrl)),
    );
  });

  test("a failed row still says why, next to its log", async () => {
    const rows = await openCard([volumeDone, failedRow]);
    const failed = within(rows[1] as HTMLElement);
    expect(failed.getByText(/manifest 404/)).toBeInTheDocument();
    expect(failed.getByRole("link", { name: "log" })).toBeInTheDocument();
  });

  test("a campaign nobody recorded the ending of still lists its volumes", async () => {
    const unknown: JobSummary = { ...reaped, phase: "Unknown" };
    const rows = await openCard(
      [{ ...volumeDone, state: "unknown", progress: null }],
      unknown,
    );
    expect(rows).toHaveLength(1);
    const row = within(rows[0] as HTMLElement);
    expect(row.getByText("unknown")).toBeInTheDocument();
    // Nothing says the result is published, so "open" falls back to the
    // volume's own source manifest rather than a file that may not be there.
    expect(row.getByRole("link", { name: "open" })).toHaveAttribute(
      "href",
      "uv.html#?manifest=" + encodeURIComponent(volumeDone.sourceUrl),
    );
    expect(row.getByRole("link", { name: "log" })).toBeInTheDocument();
  });
});

// A volume that finished but lost pages read exactly like a clean one: the
// same green chip, with "1 failed" buried in the progress line beside it
// (the product owner, 2026-09-15). Amber is the colour the header already
// uses for a campaign that published some of itself and not the rest.
describe("done, but with pages missing", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  function progress(failed: number) {
    return {
      done: 2,
      total: 3,
      failed,
      lastPage: "0003",
      stage: "done",
      updatedAt: "2026-09-14T07:00:00Z",
      ageSeconds: 97_200,
      lastError: null,
      errors: 0,
      viewerPublished: true,
    };
  }

  function lostVolume(failed: number) {
    return { ...volumeDone, progress: progress(failed) };
  }

  async function card(body: Record<string, unknown>, row: JobSummary = job) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ ...detail0, ...row, ...body })),
    );
    return render(CampaignCard, { job: row });
  }

  describe("the volume row", () => {
    async function statusChip(failed: number): Promise<HTMLElement> {
      const { container } = await card({
        failures: [],
        volumes: [lostVolume(failed)],
      });
      await vi.advanceTimersByTimeAsync(0);
      await expand();
      return container.querySelector(".status") as HTMLElement;
    }

    test("takes the warning colour when pages were lost", async () => {
      const chip = await statusChip(1);
      expect(chip).toHaveClass("status", "done", "lost");
    });

    test("says so in words, so the colour is not carrying it alone", async () => {
      const chip = await statusChip(1);
      expect(chip).toHaveAttribute("title", "done with 1 failed page");
      expect(chip).toHaveTextContent("done with 1 failed page");
    });

    test("counts more than one page in the plural", async () => {
      expect(await statusChip(4)).toHaveAttribute(
        "title",
        "done with 4 failed pages",
      );
    });

    test("a clean volume is green and says nothing extra", async () => {
      const chip = await statusChip(0);
      expect(chip).not.toHaveClass("lost");
      expect(chip).not.toHaveAttribute("title");
      expect(chip).toHaveTextContent("done");
    });
  });

  describe("the campaign header", () => {
    async function header(pagesFailed: number) {
      const succeeded: JobSummary = {
        ...job,
        phase: "Succeeded",
        counts: { total: 3, active: 0, done: 3, failed: 0 },
      };
      const { container } = await card(
        { failures: [], volumes: [], pagesDone: 2, pagesTotal: 3, pagesFailed },
        succeeded,
      );
      await vi.advanceTimersByTimeAsync(0);
      return {
        chip: container.querySelector(".chip.phase") as HTMLElement,
        section: container.querySelector(".campaign") as HTMLElement,
      };
    }

    test("a campaign that succeeded with failed pages is amber, not green", async () => {
      const { chip, section } = await header(1);
      expect(chip).toHaveClass("lost");
      expect(section).toHaveAttribute("data-health", "lost");
      expect(chip).toHaveAttribute("title", "done with 1 failed page");
      expect(chip).toHaveTextContent("done with 1 failed page");
    });

    test("a campaign that succeeded cleanly stays green", async () => {
      const { chip, section } = await header(0);
      expect(chip).not.toHaveClass("lost");
      expect(section).toHaveAttribute("data-health", "done");
      expect(chip).not.toHaveAttribute("title");
      expect(chip).toHaveTextContent("Succeeded");
    });
  });

  describe("the folded card's one-line strip", () => {
    async function strip(failed: number): Promise<HTMLElement> {
      const latest = lostVolume(failed);
      const { container } = await card({
        failures: [],
        volumes: [latest],
        latest,
      });
      await vi.advanceTimersByTimeAsync(0);
      return container.querySelector(".latest-state") as HTMLElement;
    }

    test("follows the row: amber, and it says why", async () => {
      const state = await strip(1);
      expect(state).toHaveClass("lost");
      expect(state).toHaveAttribute("title", "done with 1 failed page");
      expect(state).toHaveTextContent("done with 1 failed page");
    });

    test("a clean volume's strip is unchanged", async () => {
      const state = await strip(0);
      expect(state).not.toHaveClass("lost");
      expect(state).toHaveTextContent("done");
    });
  });
});

// Ten cards have to read like ten rows of one table, so zone 2 renders the
// same cells in the same order whatever state a campaign is in — the tracks
// are fixed lengths in CSS, and this is the DOM half of that promise.
describe("the numbers line is the same shape on every card", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function labels(row: JobSummary, body: Record<string, unknown> = {}) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          ...row,
          failures: [],
          volumes: [],
          ...body,
        }),
      ),
    );
    const { container } = render(CampaignCard, { job: row });
    await vi.advanceTimersByTimeAsync(0);
    return [...container.querySelectorAll(".metric-label")].map(
      (el) => el.textContent,
    );
  }

  test("volumes then pages, in that order, whatever the campaign is doing", async () => {
    const queued: JobSummary = { ...job, phase: "Queued" };
    const done: JobSummary = { ...job, phase: "Succeeded" };
    expect(await labels(job)).toEqual(["volumes", "pages"]);
    expect(await labels(queued)).toEqual(["volumes", "pages"]);
    expect(await labels(done)).toEqual(["volumes", "pages"]);
  });

  test("errors join the line only when there are any", async () => {
    expect(await labels(job, { errors: 2 })).toEqual([
      "volumes",
      "pages",
      "errors",
    ]);
    expect(await labels(job, { errors: 0 })).toEqual(["volumes", "pages"]);
  });

  test("each cell is label, bar and figures, in that order", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          failures: [],
          volumes: [],
          pagesDone: 5,
          pagesTotal: 8,
          pagesFailed: 3,
        }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    const cells = [...container.querySelectorAll(".metric")];
    const pagesCell = cells.find((c) => c.textContent?.startsWith("pages"))!;
    expect(
      [...pagesCell.children].map((c) => c.className.split(" ")[0]),
    ).toEqual(["metric-label", "metric-bar", "metric-figures"]);
    expect(pagesCell).toHaveTextContent("5 / 8");
    expect(pagesCell).toHaveTextContent("· 3 failed");
  });

  test("a campaign done with pages missing paints its bars amber", async () => {
    const done: JobSummary = {
      ...job,
      phase: "Succeeded",
      counts: { total: 3, active: 0, done: 3, failed: 0 },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          ...done,
          failures: [],
          volumes: [],
          pagesDone: 5,
          pagesTotal: 8,
          pagesFailed: 3,
        }),
      ),
    );
    const { container } = render(CampaignCard, { job: done });
    await vi.advanceTimersByTimeAsync(0);
    const fills = [...container.querySelectorAll(".numbers .fill")];
    expect(fills).toHaveLength(2);
    for (const fill of fills) expect(fill).toHaveClass("lost");
  });
});

// The wrapper names the failing page in its own message as often as not, and
// the API sends the page beside it: the card said it twice (the product
// owner, 2026-09-16).
describe("the problems line never names the same page twice", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function line(error: string): Promise<HTMLElement> {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          failures: [],
          volumes: [],
          pagesFailed: 1,
          lastError: {
            page: "0044",
            error,
            volume: "vol1",
            logUrl: "https://pub/status/logs/demo-v1/vol1.txt",
          },
        }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    return container.querySelector(".problems-text") as HTMLElement;
  }

  test("a message that already names its page keeps one prefix", async () => {
    const live = "page 0044: htrflow's Segmentation worker thread died";
    expect((await line(live)).textContent).toBe(live);
  });

  test("a message that does not name it is given the page", async () => {
    expect((await line("HTTP 400")).textContent).toBe("page 0044: HTTP 400");
  });
});
