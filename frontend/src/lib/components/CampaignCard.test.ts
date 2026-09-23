import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/svelte";
import { parse, type AST } from "svelte/compiler";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { JobSummary } from "$lib/api.js";
import { RELOAD_MS } from "$lib/config.js";
import { describeReason } from "$lib/reasons.js";
import CampaignCard from "./CampaignCard.svelte";
import cardSource from "./CampaignCard.svelte?raw";

// jsdom applies none of a component's scoped styles, so the layout promises
// below are read from the card's own <style> as the Svelte compiler parses
// it -- every rule, media queries included, in source order -- rather than
// by regexes that only ever saw the first rule of a name at one indentation.
type CssRule = {
  selectors: string[];
  media: string | null;
  decls: [string, string][];
};

const squash = (text: string) => text.replace(/\s+/g, " ").trim();

const cardRules: CssRule[] = (() => {
  const rules: CssRule[] = [];
  const walk = (
    nodes: (AST.CSS.Rule | AST.CSS.Atrule | AST.CSS.Declaration)[],
    media: string | null,
  ) => {
    for (const node of nodes) {
      if (node.type === "Atrule" && node.name === "media" && node.block)
        walk(node.block.children, squash(node.prelude));
      if (node.type !== "Rule") continue;
      rules.push({
        selectors: node.prelude.children.map((c) =>
          squash(cardSource.slice(c.start, c.end)),
        ),
        media,
        decls: node.block.children.flatMap((d) =>
          d.type === "Declaration"
            ? [[d.property, squash(d.value)] as [string, string]]
            : [],
        ),
      });
    }
  };
  walk(parse(cardSource, { modern: true }).css?.children ?? [], null);
  return rules;
})();

const PHONE = "(max-width: 520px)";

/**
 * What `selector` (exactly that selector) ends up with, at full width or at
 * `media`: the top-level rules and that media query's, the later one of two
 * winning as in the cascade.
 */
function cssOf(selector: string, media: string | null = null) {
  const out = new Map<string, string>();
  for (const rule of cardRules)
    if (
      (rule.media === null || rule.media === media) &&
      rule.selectors.includes(selector)
    )
      for (const [property, value] of rule.decls) out.set(property, value);
  return out;
}

/** Every declaration of every rule whose subject is `cls`, anywhere. */
function declsOn(cls: string): [string, string][] {
  const subject = new RegExp(`\\.${cls}(?![\\w-])[^\\s>+~]*$`);
  return cardRules
    .filter((r) => r.selectors.some((sel) => subject.test(sel)))
    .flatMap((r) => r.decls);
}

/** A grid-template-columns value as its tracks, brackets kept whole. */
function tracks(value: string | undefined): string[] {
  const out = [""];
  let depth = 0;
  for (const ch of value ?? "") {
    depth += ch === "(" ? 1 : ch === ")" ? -1 : 0;
    if (ch === " " && depth === 0) out.push("");
    else out[out.length - 1] += ch;
  }
  return out;
}

/** A grid-template-areas value as its rows of names. */
function areas(value: string | undefined): string[][] {
  return [...(value ?? "").matchAll(/"([^"]*)"/g)].map((m) =>
    squash(m[1] ?? "").split(" "),
  );
}

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
  pagesCoverage: { counted: 0, of: 0 },
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

  test("the id opens the viewer; the log and the manifest are icons", async () => {
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
    // done: the id opens the published result in the viewer
    expect(done.getByRole("link", { name: "vol0" })).toHaveAttribute(
      "href",
      "uv.html#?manifest=" +
        encodeURIComponent("https://pub/htr-test/demo-v1/vol0/iiif.json"),
    );
    expect(
      done.getByRole("link", { name: "manifest for vol0" }),
    ).toHaveAttribute("href", "https://iiif.example.org/vol0/manifest");
    expect(
      done.getByRole("link", { name: "run log for vol0" }),
    ).toBeInTheDocument();
    // The glyphs are decoration; the label is what names the link.
    expect(
      done.getByRole("link", { name: "run log for vol0" }).querySelector("svg"),
    ).toHaveAttribute("aria-hidden", "true");
    // The source first, then its run log: what the volume came from, then
    // what happened to it.
    expect(
      done
        .getByRole("link", { name: "manifest for vol0" })
        .compareDocumentPosition(
          done.getByRole("link", { name: "run log for vol0" }),
        ) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    // not done, but it has a source: the id opens the source manifest
    const failed = within(rows[1] as HTMLElement);
    expect(failed.getByRole("link", { name: "vol1" })).toHaveAttribute(
      "href",
      "uv.html#?manifest=" +
        encodeURIComponent("https://iiif.example.org/vol1/manifest"),
    );

    // no source at all: the id is plain text saying so, the manifest slot
    // is empty, and the run log is still there.
    const images = within(rows[2] as HTMLElement);
    expect(images.queryByRole("link", { name: "vol2" })).toBeNull();
    expect(images.getByTitle(/nothing to open there yet/)).toHaveTextContent(
      "vol2",
    );
    expect(images.queryByRole("link", { name: /^manifest for/ })).toBeNull();
    expect(
      images.getByRole("link", { name: "run log for vol2" }),
    ).toBeInTheDocument();
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
    const numbers = within(
      container.querySelector(".card-body") as HTMLElement,
    );
    expect(numbers.getByText("pages")).toBeInTheDocument();
    expect(numbers.getByText(/137 \/ 638/)).toBeInTheDocument();
    await expand();

    // The row says the same numbers in the same shape, and what the numbers
    // cannot say beside them.
    const row = within(screen.getAllByRole("row").slice(1)[0] as HTMLElement);
    expect(row.getByText("137 / 638")).toBeInTheDocument();
    expect(
      row.getByText("processing pages · updated 12 s ago"),
    ).toBeInTheDocument();
    // A volume with nothing to report shows the state word and an em dash.
    const done = within(screen.getAllByRole("row").slice(1)[1] as HTMLElement);
    expect(done.queryByText(/updated/)).toBeNull();
    expect(done.getByText("—")).toBeInTheDocument();
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
      within(rows[0] as HTMLElement).getByRole("link", { name: /^vol/ }),
    ).toHaveAttribute(
      "href",
      "uv.html#?manifest=" +
        encodeURIComponent("https://pub/htr-test/demo-v1/vol1/iiif.json"),
    );
    // Nothing published yet: still the source manifest, as before.
    expect(
      within(rows[1] as HTMLElement).getByRole("link", { name: /^vol/ }),
    ).toHaveAttribute(
      "href",
      "uv.html#?manifest=" +
        encodeURIComponent("https://iiif.example.org/vol1/manifest"),
    );
  });

  test("a sourceUrl that is not an http(s) URL never reaches the card", async () => {
    // volumes.txt is a file humans edit in a git repo. The schema reads the
    // field on its own ($lib/api): one the page cannot use is no link, and
    // the rest of the card -- this volume included -- still draws. Refusing
    // the whole detail over it left the card an error for ever (2026-09-23
    // audit). The card's own checks stay as the last step.
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

    expect(screen.queryByRole("link", { name: /^manifest for/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /^vol/ })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(
      screen.getByRole("link", { name: `run log for ${hostile.id}` }),
    ).toBeInTheDocument();
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
      name: "run log for vol0",
    });
    expect(doneLog).toHaveAttribute(
      "href",
      "log?log=" +
        encodeURIComponent(volumeDone.logUrl) +
        "&manifest=" +
        encodeURIComponent(volumeDone.manifestUrl),
    );
    const failedLog = within(rows[1] as HTMLElement).getByRole("link", {
      name: "run log for vol1",
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

  test("while folded, the API's latest volume keeps its links in reach", async () => {
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
    expect(screen.getByRole("link", { name: "vol260" })).toHaveAttribute(
      "href",
      "uv.html#?manifest=" +
        encodeURIComponent("https://iiif.example.org/vol260/manifest"),
    );
    expect(
      screen.getByRole("link", { name: "manifest for vol260" }),
    ).toHaveAttribute("href", "https://iiif.example.org/vol260/manifest");
    expect(
      screen.getByRole("link", { name: "run log for vol260" }),
    ).toHaveAttribute(
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
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    expect(container.querySelector(".row.latest")).toBeNull();
    expect(screen.queryByRole("link", { name: /^run log for/ })).toBeNull();
  });

  test("zone 3 names every failed volume and why", async () => {
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

    // Zone 3: each failure as `id: sentence`. Sentences, not the
    // wrapper's fields — no reader ever sees a stage name, a `permanent`
    // flag or a Python list repr.
    const line = container.querySelector(".problems-text") as HTMLElement;
    expect(line).toHaveTextContent(
      "vol1: Failed while loading the model: model not found. This volume " +
        "will not be retried — fix the cause, then put the volume in a new " +
        "campaign. · vol2: 2 pages are missing from the results (p012, " +
        "p045), and the volume has used all its retries — put it in a new " +
        "campaign to redo them.",
    );
    // Nothing is clipped, so nothing needs a mouse-only `title` copy.
    expect(container.querySelector(".problems")).not.toHaveAttribute("title");
  });

  test("a failed volume is never promised a retry; an active one is (3078)", async () => {
    // Both pods were SIGTERMed, a transient cause. The failed one is in the
    // Job's failedIndexes -- its backoffLimitPerIndex is spent -- and the
    // active one is between attempts. Only the second comes back.
    const drained = { stage: "stream", permanent: false, error: "SIGTERM" };
    const failed = { ...volumeFailed, reason: drained };
    const retrying = {
      ...volumeFailed,
      index: 2,
      id: "vol2",
      state: "active",
      reason: drained,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          failures: [failed],
          volumes: [failed, retrying],
        }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    // Folded, the failure is on the problems line.
    expect(container.querySelector(".problems-text")).toHaveTextContent(
      "vol1: The pod was stopped by the cluster (a node drain or a pause), " +
        "and the volume has used all its retries — put it in a new campaign " +
        "to run it again.",
    );
    await expand();
    const [failedNote, retryingNote] = [
      ...container.querySelectorAll(".row-note"),
    ];
    expect(failedNote).toHaveTextContent("has used all its retries");
    expect(failedNote).not.toHaveTextContent("will be retried");
    expect(retryingNote).toHaveTextContent(
      "The pod was stopped by the cluster (a node drain or a pause); the " +
        "volume will be retried.",
    );
  });

  test("a long problems line shows three, and the rest behind a button (3080)", async () => {
    // Up to 50 failures used to sit in one clipped line: the second one on
    // was unreadable on a phone or from a keyboard, and a Tab could land on
    // a link nobody could see (the 2026-09-17 audit). Now the line wraps,
    // and past three the rest wait behind a button rather than burying the
    // volumes under a paragraph. A hidden problem is not rendered at all,
    // so there is no hidden link to focus.
    const many = Array.from({ length: 5 }, (_, i) => ({
      ...volumeFailed,
      index: i + 10,
      id: `vol${i + 10}`,
    }));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: many, volumes: [] }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    const line = () => container.querySelector(".problems-text") as HTMLElement;
    const ids = () =>
      [...line().querySelectorAll("a")].map((a) => a.textContent);
    expect(ids()).toEqual(["vol10", "vol11", "vol12"]);
    const more = screen.getByRole("button", { name: "2 more" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    await fireEvent.click(more);
    expect(ids()).toEqual(["vol10", "vol11", "vol12", "vol13", "vol14"]);
    const fewer = screen.getByRole("button", { name: "fewer" });
    expect(fewer).toHaveAttribute("aria-expanded", "true");
    expect(fewer).toHaveAttribute("aria-controls", line().id);
  });

  test("three problems or fewer need no button", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [volumeFailed], volumes: [] }),
      ),
    );
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.queryByRole("button", { name: /more$/ })).toBeNull();
  });

  test("no sentence on the card is clipped to one line (3080)", () => {
    // A line of sentences, or one that holds links, must wrap rather than
    // cut -- in every rule that styles it, at any width.
    for (const cls of ["problems-text", "row-note-text", "vreason"]) {
      const decls = declsOn(cls);
      expect(decls, cls).toContainEqual(["overflow-wrap", "anywhere"]);
      for (const [property, value] of decls) {
        const clips =
          (property === "white-space" && /nowrap|pre\b/.test(value)) ||
          (/^overflow(-[xy])?$/.test(property) && /hidden|clip/.test(value)) ||
          property === "text-overflow" ||
          /line-clamp$/.test(property);
        expect(clips, `${cls} { ${property}: ${value} }`).toBe(false);
      }
    }
  });

  test("each failed volume on the problems line links to its own run log", async () => {
    // The callout this line replaced gave every failure its own log link,
    // and losing it meant a failed volume you could read about but not open
    // unless it happened to be the one the last page error came from
    // (2026-09-16 review).
    const offPage = { ...volumeFailed, index: 7, id: "vol7" };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          failures: [volumeFailed, offPage],
          volumes: [],
        }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);

    const line = container.querySelector(".problems-text") as HTMLElement;
    const links = [...line.querySelectorAll("a")];
    expect(links.map((a) => a.textContent)).toEqual(["vol1", "vol7"]);
    expect(links[0]).toHaveAttribute(
      "href",
      `log?log=${encodeURIComponent(volumeFailed.logUrl)}` +
        `&manifest=${encodeURIComponent(volumeFailed.manifestUrl)}&live=1`,
    );
    // The sentences are text around those links.
    expect(line).toHaveTextContent(/^vol1: .* · vol7: /);
  });

  test("the problems line is the real text, reachable and not duplicated", async () => {
    // Clipping with overflow does not take text out of the accessibility
    // tree, so the line needs no hidden second copy -- and a copy beside
    // links would be read twice (2026-09-16 review).
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [volumeFailed], volumes: [] }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    const line = container.querySelector(".problems-text") as HTMLElement;
    expect(line).not.toHaveAttribute("aria-hidden");
    expect(container.querySelector(".problems .sr-only")).toBeNull();
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

  test("a pipeline with no models still names itself in the footer", async () => {
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
    // The footer always names the pipeline; the models half is what a
    // pipeline with none leaves out.
    expect(container.querySelector(".models")).toBeNull();
    expect(container.querySelector(".card-meta")).not.toBeNull();
    expect(container.querySelector(".provenance")).toHaveTextContent(
      "pipeline demo-v1",
    );
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

  // The live page read "10:56 →11:00": the sr-only span next to the arrow
  // swallowed the whitespace after it (2026-09-16 review).
  test("the arrow has a space on each side of it", async () => {
    const done: JobSummary = {
      ...job,
      phase: "Succeeded",
      createdAt: "2026-01-01T10:56:00Z",
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
    const seen = (when.textContent ?? "").replace(/created|, finished/g, "");
    expect(seen.replace(/\s+/g, " ").trim()).toMatch(
      /\d{2}:\d{2} → \d{2}:\d{2}$/,
    );
  });

  test("a running campaign's open end has the same spacing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [], volumes: [] }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    const when = container.querySelector(".when") as HTMLElement;
    const seen = (when.textContent ?? "").replace(
      /created|, still running/g,
      "",
    );
    expect(seen.replace(/\s+/g, " ").trim()).toMatch(/\d{2}:\d{2} → …$/);
  });

  test("a finish with no creation date reads without a stray comma", async () => {
    // The API sends `createdAt` for every row it builds from a Job, but a
    // record with only a finish is a shape the schema allows.
    const orphan: JobSummary = {
      ...job,
      phase: "Succeeded",
      createdAt: null,
      finishedAt: "2026-01-01T11:20:00Z",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, ...orphan, failures: [], volumes: [] }),
      ),
    );
    const { container } = render(CampaignCard, { job: orphan });
    await vi.advanceTimersByTimeAsync(0);
    const when = container.querySelector(".when") as HTMLElement;
    // The comma joins two halves; with only one there is nothing to join.
    expect(when.querySelector(".sr-only")).toHaveTextContent(/^finished$/);
    expect(when.textContent).not.toContain("→");
  });

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
      expect(describeReason(reason, true)).toBe(sentence);
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
    expect(within(ident).getByText("Running")).toBeInTheDocument();
    expect(within(ident).getByText("htr-test/kyrk")).toBeInTheDocument();
    // The pipeline is provenance, and sits in the card's footer instead of
    // competing with the campaign's state for the header row (the product
    // owner, 2026-09-16).
    expect(within(ident).queryByText("demo-v1")).toBeNull();
    const footer = container.querySelector(".card-meta") as HTMLElement;
    expect(within(footer).getByText("demo-v1")).toBeInTheDocument();
    expect(footer.compareDocumentPosition(ident)).toBe(
      Node.DOCUMENT_POSITION_PRECEDING,
    );

    const numbers = container.querySelector(".card-body") as HTMLElement;
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
    expect(screen.getByRole("link", { name: "log" })).toHaveAttribute(
      "href",
      "log?log=https%3A%2F%2Fpub%2Fstatus%2Flogs%2Fdemo-v1%2Fvol1.txt&live=1",
    );
  });

  test("errors with no sentence behind them are a number, not a line", async () => {
    renderWith({ pagesFailed: 0, errors: 3, lastError: null });
    await vi.advanceTimersByTimeAsync(0);
    // They ride on the pages figures rather than taking a column that lines
    // up with nothing (the product owner, 2026-09-16); the problems line has
    // nothing to say that the numbers have not.
    const rows = [...document.querySelectorAll(".row.totals")];
    const pages = rows.find((r) => r.textContent?.startsWith("pages"));
    expect(pages?.querySelector(".c-lost")).toHaveTextContent("3 errors");
    expect(document.querySelector(".problems")).toBeNull();
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
    const cells = [...container.querySelectorAll(".row.totals")];
    const pagesCell = cells.find((c) => c.textContent?.startsWith("pages"));
    expect(pagesCell).toHaveTextContent("—");
  });

  test("page totals not yet every volume's say how many they cover", async () => {
    // A lost page in a volume the API has not read yet would otherwise
    // hide behind a clean-looking total (3076).
    stubPolls(
      {
        pagesDone: 90,
        pagesTotal: 100,
        pagesCoverage: { counted: 120, of: 500 },
      },
      {
        pagesDone: 90,
        pagesTotal: 100,
        pagesCoverage: { counted: 500, of: 500 },
      },
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    const pagesCell = () =>
      [...container.querySelectorAll(".row.totals")].find((c) =>
        c.textContent?.startsWith("pages"),
      );
    expect(pagesCell()).toHaveTextContent("counted in 120 of 500 volumes");
    await vi.advanceTimersByTimeAsync(RELOAD_MS);
    expect(pagesCell()).not.toHaveTextContent("counted in");
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
    expect(container.querySelectorAll(".vfigures.bump")).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(RELOAD_MS);
    expect(screen.getByText("151 / 638")).toBeInTheDocument();
    const flashed = container.querySelectorAll(".vfigures.bump");
    expect(flashed).toHaveLength(1); // vol3 sent the same count back
    expect(flashed[0]).toHaveTextContent("151 / 638");
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
    return within(row).queryByRole("link", { name: "vol0" });
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
    expect(screen.queryByRole("link", { name: "vol0" })).toBeNull();
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

  test("every row is there, with its id link and its two icons", async () => {
    const rows = await openCard([volumeDone, failedRow]);
    expect(rows).toHaveLength(2);
    const done = within(rows[0] as HTMLElement);
    expect(done.getByRole("link", { name: "vol0" })).toHaveAttribute(
      "href",
      "uv.html#?manifest=" + encodeURIComponent(volumeDone.iiifUrl),
    );
    expect(
      done.getByRole("link", { name: "manifest for vol0" }),
    ).toHaveAttribute("href", volumeDone.sourceUrl);
    expect(
      done.getByRole("link", { name: "run log for vol0" }),
    ).toHaveAttribute(
      "href",
      expect.stringContaining(encodeURIComponent(volumeDone.logUrl)),
    );
  });

  test("a failed row still says why, next to its log", async () => {
    const rows = await openCard([volumeDone, failedRow]);
    const failed = within(rows[1] as HTMLElement);
    expect(failed.getByText(/manifest 404/)).toBeInTheDocument();
    expect(
      failed.getByRole("link", { name: "run log for vol1" }),
    ).toBeInTheDocument();
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
    // Nothing says the result is published, so the id falls back to the
    // volume's own source manifest rather than a file that may not be there.
    expect(row.getByRole("link", { name: "vol0" })).toHaveAttribute(
      "href",
      "uv.html#?manifest=" + encodeURIComponent(volumeDone.sourceUrl),
    );
    expect(
      row.getByRole("link", { name: "run log for vol0" }),
    ).toBeInTheDocument();
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
    return [...container.querySelectorAll(".row.totals .c-label")].map(
      (el) => el.textContent,
    );
  }

  test("volumes then pages, in that order, whatever the campaign is doing", async () => {
    const queued: JobSummary = { ...job, phase: "Queued" };
    const done: JobSummary = { ...job, phase: "Succeeded" };
    const two = ["volumes · 1 active", "pages"];
    expect(await labels(job)).toEqual(two);
    expect(await labels(queued)).toEqual(["volumes", "pages"]);
    expect(await labels(done)).toEqual(["volumes", "pages"]);
  });

  test("errors join the line under the pages bar, and only when there are any", async () => {
    expect(await labels(job, { errors: 2 })).toEqual([
      "volumes · 1 active",
      "pages",
    ]);
    const lost = () =>
      [...document.querySelectorAll(".row.totals")]
        .filter((r) => r.textContent?.startsWith("pages"))
        .pop()
        ?.querySelector(".c-lost")?.textContent ?? "";
    expect(lost()).toContain("2 errors");
    await labels(job, { errors: 1 });
    expect(lost()).toContain("1 error");
    await labels(job, { errors: 0 });
    expect(lost()).not.toContain("error");
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
    const fills = [...container.querySelectorAll(".row.totals .fill")];
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
});

// The live PoC read "5 / 8· 3 failed": Svelte trims the whitespace before an
// element, so the separator had nothing in front of it (2026-09-16 review).
// And `counts.active` had no home at all after the header line went.
describe("the figures beside a bar", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function cell(
    name: "volumes" | "pages",
    row: JobSummary,
    body: Record<string, unknown> = {},
  ): Promise<HTMLElement> {
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
    const cells = [...container.querySelectorAll(".row.totals")];
    return cells
      .find((c) => c.textContent?.startsWith(name))
      ?.querySelector(".c-fraction") as HTMLElement;
  }

  /** The whole totals row, for the tests that read more than its fraction. */
  async function totalsRowFor(
    name: "volumes" | "pages",
    row: JobSummary,
    body: Record<string, unknown> = {},
  ): Promise<HTMLElement> {
    cleanup();
    await cell(name, row, body);
    return [...document.querySelectorAll(".row.totals")].find((c) =>
      c.textContent?.startsWith(name),
    ) as HTMLElement;
  }

  test("the fraction is the fraction and nothing else", async () => {
    const figures = await cell("pages", job, {
      pagesDone: 5,
      pagesTotal: 8,
      pagesFailed: 3,
    });
    expect(figures.textContent?.trim()).toBe("5 / 8");
  });

  test("a running campaign says how many volumes are in flight", async () => {
    const row = await totalsRowFor("volumes", {
      ...job,
      phase: "Running",
      counts: { total: 5, active: 1, done: 2, failed: 0 },
    });
    expect(row.querySelector(".c-label")?.textContent?.trim()).toBe(
      "volumes · 1 active",
    );
    expect(row.querySelector(".c-fraction")?.textContent?.trim()).toBe("2 / 5");
  });

  test("what failed goes under the bar, what is running beside the label", async () => {
    const row = await totalsRowFor("volumes", {
      ...job,
      phase: "Running",
      counts: { total: 5, active: 1, done: 2, failed: 1 },
    });
    expect(row.querySelector(".c-label")?.textContent?.trim()).toBe(
      "volumes · 1 active",
    );
    expect(row.querySelector(".c-lost")?.textContent?.trim()).toBe("1 failed");
  });

  test("nothing in flight, nothing said", async () => {
    const row = await totalsRowFor("volumes", {
      ...job,
      phase: "Running",
      counts: { total: 5, active: 0, done: 5, failed: 0 },
    });
    expect(row.querySelector(".c-label")?.textContent?.trim()).toBe("volumes");
    expect(row.querySelector(".c-lost")).toBeNull();
  });

  test("a campaign that is over never says active, whatever the count says", async () => {
    // A reaped campaign's record can carry a stale `active`; the Job is gone.
    const row = await totalsRowFor("volumes", {
      ...job,
      phase: "Succeeded",
      jobGone: true,
      counts: { total: 5, active: 2, done: 5, failed: 0 },
    });
    expect(row.querySelector(".c-label")?.textContent?.trim()).toBe("volumes");
  });
});

// "I think we should be able to do something about the status column too?"
// (the product owner, 2026-09-16). It said "DONE one-bad" in green with no
// numbers at all on the folded strip, and "done" in the pill with a second
// sentence beside it in the table. Both now say per volume what zone 2 says
// per campaign.
describe("the volume status column", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  function progress(over: Record<string, unknown> = {}) {
    return {
      done: 2,
      total: 3,
      failed: 0,
      lastPage: "0003",
      stage: "done",
      updatedAt: "2026-09-14T07:00:00Z",
      ageSeconds: null,
      lastError: null,
      errors: 0,
      viewerPublished: true,
      ...over,
    };
  }

  async function strip(v: unknown): Promise<HTMLElement> {
    cleanup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [], volumes: [v], latest: v }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    return container.querySelector(".latest") as HTMLElement;
  }

  async function cell(v: unknown): Promise<HTMLElement> {
    cleanup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [], volumes: [v] }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    await expand();
    return container.querySelector(".row.volume") as HTMLElement;
  }

  const done = { ...volumeDone, progress: progress() };
  const lost = { ...volumeDone, progress: progress({ failed: 1 }) };
  const active = {
    ...volumeDone,
    state: "active",
    progress: progress({
      done: 137,
      total: 638,
      stage: "stream",
      ageSeconds: 12,
    }),
  };
  const unknown = { ...volumeDone, progress: null };

  test("the open list is an ARIA table, with headers nobody has to see", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [], volumes: [done] }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    await expand();
    expect(container.querySelector('[role="table"]')).toHaveAttribute(
      "aria-label",
      "Volumes in campaign kyrk",
    );
    expect(
      [...container.querySelectorAll('[role="columnheader"]')].map(
        (c) => c.textContent,
      ),
    ).toEqual(["volume", "links", "progress", "pages", "status"]);
  });

  test("the manifest keeps its slot even when the volume has none", async () => {
    const noSource = { ...volumeDone, sourceUrl: null, progress: progress() };
    for (const el of [await strip(noSource), await cell(noSource)]) {
      const row = el.closest("tr") ?? el;
      expect(row.querySelectorAll(".slot")).toHaveLength(2);
      expect(row.querySelectorAll(".vicon")).toHaveLength(1);
    }
  });

  // A volume that finished but lost pages is not a failure -- it published
  // -- and not a clean run either: amber, and the words say it too, so the
  // colour is not carrying it alone (WCAG 1.4.1).
  test.each([
    ["the strip", 0, null],
    ["the strip", 1, "done with 1 failed page"],
    ["a volume row", 0, null],
    ["a volume row", 1, "done with 1 failed page"],
    ["a volume row", 4, "done with 4 failed pages"],
  ] as const)(
    "%s of a done volume that lost %i pages",
    async (where, failed, said) => {
      const v = { ...volumeDone, progress: progress({ failed }) };
      const el = where === "the strip" ? await strip(v) : await cell(v);
      const word = el.querySelector(".status") as HTMLElement;
      expect(word).toHaveClass("done");
      expect(el.querySelector(".c-fraction")?.textContent?.trim()).toBe(
        "2 / 3",
      );
      if (said === null) {
        expect(word).not.toHaveClass("lost");
        expect(word).not.toHaveAttribute("title");
        expect(word.textContent?.trim()).toBe("done");
        expect(el.querySelector(".c-lost")).toBeNull();
      } else {
        expect(word).toHaveClass("lost");
        expect(word).toHaveAttribute("title", said);
        expect(word.querySelector(".sr-only")).toHaveTextContent(said);
        expect(el.querySelector(".c-lost")?.textContent?.trim()).toBe(
          `${failed} failed`,
        );
      }
    },
  );

  test("an active volume: its fraction, its bar and what it is doing", async () => {
    for (const el of [await strip(active), await cell(active)]) {
      expect(el.querySelector(".status")).toHaveClass("active");
      expect(el.querySelector(".c-fraction")).toHaveTextContent("137 / 638");
      expect(el.querySelector('[role="progressbar"]')).not.toBeNull();
      expect(el.querySelector(".vprogress")).toHaveTextContent(
        "processing pages · updated 12 s ago",
      );
    }
  });

  test("a volume with nothing read yet says so with an em dash", async () => {
    for (const el of [await strip(unknown), await cell(unknown)]) {
      expect(el.querySelector(".c-fraction")).toHaveTextContent("—");
      expect(el.querySelector('[role="progressbar"]')).toBeNull();
    }
  });

  test("a failed volume: red word, and the reason on the strip", async () => {
    const line = await strip(volumeFailed);
    expect(line.querySelector(".status")).toHaveClass("failed");
    const reason = line.querySelector(".vreason") as HTMLElement;
    expect(reason).toHaveTextContent("Failed while loading the model");
    // Wrapped, not clipped: the sentence is all there to read, on a phone
    // and from a keyboard, so it needs no mouse-only copy (3080).
    expect(reason).not.toHaveAttribute("title");
    // The sentence sits with the id, where the words go; the fraction slot
    // still holds the column open.
    expect(line.querySelector(".c-label .vreason")).not.toBeNull();
  });

  test("...and in the open list it is a line under the row", async () => {
    // The id's track is capped at 16rem, which would clip a sentence to
    // nothing, so it spans the row underneath instead (2026-09-16).
    const row = await cell(volumeFailed);
    expect(row.querySelector(".status")).toHaveClass("failed");
    expect(row.querySelector(".c-label .vreason")).toBeNull();
    expect(row.querySelector(".row-note")).toHaveTextContent(
      "Failed while loading the model",
    );
  });

  test("the numbers are said once, not twice, in one cell", async () => {
    const td = await cell(active);
    expect(td.textContent).not.toMatch(/137 \/ 638.*137 \/ 638/s);
    expect(td.querySelector(".vprogress")?.textContent).not.toContain("638");
  });
});

// "look at the status now, they are all wobbly" (the product owner,
// 2026-09-16): the icons floated at a different x on every card because they
// sat between a variable-width id and a right-aligned status, and the pill's
// left edge moved with the width of the figures beside it.
describe("a volume line is the same shape on every row and every card", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  function vol(id: string, over: Record<string, unknown> = {}) {
    return {
      ...volumeDone,
      id,
      progress: {
        done: 2,
        total: 3,
        failed: 0,
        lastPage: "0003",
        stage: "done",
        updatedAt: "2026-09-14T07:00:00Z",
        ageSeconds: 169_200,
        lastError: null,
        errors: 0,
        viewerPublished: true,
      },
      ...over,
    };
  }

  async function card(volumes: unknown[], latest: unknown = null) {
    cleanup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [], volumes, latest }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    return container;
  }

  test.each([
    ["the strip", ".row.latest"],
    ["a volume row", ".row.volume"],
    ["a totals row", ".row.totals"],
  ] as const)(
    "%s is the same five tracks in the same order, what it lost under them",
    async (where, selector) => {
      // One order everywhere: the id, its icons, the bar, the fraction it
      // draws and the pill at the edge, so every number on the card sits in
      // one column and every pill in another. A totals row keeps the icon
      // and pill cells, empty, to hold those columns open.
      const lost = vol("vol0");
      lost.progress.failed = 1;
      const container = await card([lost], lost);
      if (where === "a volume row") await expand();
      const row = container.querySelector(selector) as HTMLElement;
      expect([...row.children].map((c) => c.className.split(" ")[0])).toEqual([
        "c-label",
        "c-links",
        "c-bar",
        "c-fraction",
        "c-status",
        "c-lost",
      ]);
      const pill = row.querySelector(".c-status .status");
      if (where === "a totals row") {
        expect(pill).toBeNull();
        expect(row.querySelector(".c-links")?.textContent?.trim()).toBe("");
      } else expect(pill).not.toBeNull();
    },
  );

  test("a long id and a short one put their icons in the same place", async () => {
    // The icons are a track of their own, not a thing that follows the text:
    // two rows whose ids differ in length line up all the same. jsdom lays
    // nothing out, so what is asserted is where they live -- in the links
    // cell, the grid's fixed `--icons` track, and never inside the id's.
    const container = await card([vol("a"), vol("R0001203-part-4")]);
    await expand();
    const rows = [...container.querySelectorAll(".row.volume")];
    expect(rows).toHaveLength(2);
    for (const row of rows) {
      expect(row.querySelectorAll(".c-links .vicon")).toHaveLength(2);
      expect(row.querySelector(".c-label .vicon")).toBeNull();
    }
  });

  test("a finished volume says nothing about when it last spoke", async () => {
    const container = await card([vol("vol0")], vol("vol0"));
    expect(container.querySelector(".row.volume .vprogress")).toBeNull();
    await expand();
    expect(container.querySelector(".row.volume .vprogress")).toBeNull();
    // The fraction is still there; it is only the clock that goes.
    expect(
      container.querySelector(".row.volume .c-fraction"),
    ).toHaveTextContent("2 / 3");
  });

  test("a poll that moves the figures changes nothing but the figures", async () => {
    let done = 2;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          failures: [],
          volumes: [
            {
              ...vol("vol2"),
              state: "active",
              progress: {
                ...vol("vol2").progress,
                done,
                stage: "stream",
                ageSeconds: 12,
              },
            },
          ],
        }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    await expand();
    // `className` on an SVG element is not a string, so read the attribute.
    const shape = () =>
      [...container.querySelectorAll(".row.volume *")].map((el) =>
        (el.getAttribute("class") ?? "").replace(/\s*bump\s*/, " ").trim(),
      );
    const before = shape();
    done = 3;
    await vi.advanceTimersByTimeAsync(RELOAD_MS);
    expect(
      container.querySelector(".row.volume .c-fraction"),
    ).toHaveTextContent("3 / 3");
    // Same elements, same classes but for the highlight, so nothing resizes.
    expect(shape()).toEqual(before);
    expect(container.querySelector(".vfigures.bump")).not.toBeNull();
  });
});

// Two notes from the review of the zones round (2026-09-16).
describe("the volume line at a phone's width, and what it says it cannot do", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function rowFor(v: unknown) {
    cleanup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [], volumes: [v] }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    await expand();
    return container;
  }

  // The card body is one grid, so a phone gets the same tracks folded to
  // two columns rather than a table scrolling sideways on a 390px screen.
  test("a phone folds the same tracks to two lines", () => {
    // The id and its figures on line 1, then the icons, the short bar, the
    // fraction and the pill on line 2.
    const [line1, line2] = areas(
      cssOf(".row", PHONE).get("grid-template-areas"),
    );
    expect(line1).toEqual(["label", "label", "label", "label", "label"]);
    expect(line2).toEqual([".", "links", "bar", "fraction", "status"]);
    // The words must not clip there: there is a whole line for them.
    const id = cssOf(".vid-line", PHONE);
    expect(id.get("white-space")).toBe("normal");
    expect(id.get("overflow")).toBe("visible");
  });

  test.each([
    ["pending" as const, /nothing to open there yet/],
    ["active" as const, /nothing to open there yet/],
    ["failed" as const, /no viewer manifest for this volume/],
    ["unknown" as const, /no viewer manifest for this volume/],
  ])(
    "a %s volume with nothing to open says so in the right tense",
    async (state, wording) => {
      // "yet" is a promise; a volume that failed or was never recorded is
      // not going to publish one (2026-09-16 review).
      const container = await rowFor({
        ...volumeDone,
        state,
        sourceUrl: null,
        progress: null,
      });
      expect(container.querySelector(".vid-name")).toHaveAttribute(
        "title",
        expect.stringMatching(wording),
      );
    },
  );
});

// "we have partially failed, maybe we should have partially succeeded also"
// (the product owner, 2026-09-16): a campaign whose Job succeeded but whose
// volumes lost pages wore the word "Succeeded" painted amber — the word and
// the colour saying different things.
describe("what the phase chip calls a campaign", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function chip(phase: JobSummary["phase"], pagesFailed = 0) {
    cleanup();
    const row: JobSummary = {
      ...job,
      phase,
      counts: { total: 3, active: 0, done: 3, failed: 0 },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          ...row,
          failures: [],
          volumes: [],
          pagesDone: 5,
          pagesTotal: 8,
          pagesFailed,
        }),
      ),
    );
    const { container } = render(CampaignCard, { job: row });
    await vi.advanceTimersByTimeAsync(0);
    return {
      chip: container.querySelector(".chip.phase") as HTMLElement,
      accent: container.querySelector(".campaign") as HTMLElement,
    };
  }

  // The two "partially" words are a pair and mean different losses:
  // PartiallyFailed is whole VOLUMES that never published, "partially
  // succeeded" every volume finishing without some of its PAGES. Each mixes
  // the amber with where it ended -- on the chip and on the card's accent --
  // and the tooltip and the screen-reader sentence say how many.
  test.each([
    ["Succeeded", 0, "Succeeded", null, null, "succeeded", null, "done"],
    [
      "Succeeded",
      1,
      "partially succeeded",
      "every volume finished, 1 page failed",
      "done with 1 failed page",
      "lost",
      "success",
      "partly-succeeded",
    ],
    [
      "Succeeded",
      3,
      "partially succeeded",
      "every volume finished, 3 pages failed",
      "done with 3 failed pages",
      "lost",
      "success",
      "partly-succeeded",
    ],
    [
      "PartiallyFailed",
      0,
      "partially failed",
      null,
      null,
      "partiallyfailed",
      "destructive",
      "partly-failed",
    ],
    ["Failed", 0, "Failed", null, null, "failed", null, "failed"],
  ] as const)(
    "%s with %i failed pages reads %s",
    async (phase, pagesFailed, word, title, spoken, cls, mix, health) => {
      const { chip: el, accent } = await chip(phase, pagesFailed);
      const shown = [...el.childNodes]
        .filter((n) => !(n instanceof Element && n.matches(".sr-only")))
        .map((n) => n.textContent)
        .join("")
        .trim();
      expect(shown).toBe(word);
      expect(el).toHaveClass(cls);
      expect(el.classList.contains("lost")).toBe(cls === "lost");
      if (title === null) expect(el).not.toHaveAttribute("title");
      else expect(el).toHaveAttribute("title", title);
      expect(el.querySelector(".sr-only")?.textContent ?? null).toBe(spoken);
      if (mix === null) expect(el).not.toHaveAttribute("data-mix");
      else expect(el).toHaveAttribute("data-mix", mix);
      expect(accent).toHaveAttribute("data-health", health);
    },
  );
});

// A campaign that has finished cannot change, and every detail call lists
// pods, reads ConfigMaps and up to 32 progress files: a page of old
// campaigns polling each minute was load that grew with the history and
// bought nothing (the 2026-09-17 audit, 3079).
describe("a finished campaign's card stops asking", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  function detailFor(row: JobSummary) {
    return vi.fn(async () =>
      jsonResponse({ ...detail0, ...row, failures: [], volumes: [] }),
    );
  }

  test.each([
    ["Succeeded", false],
    ["PartiallyFailed", false],
    ["Failed", false],
    ["Unknown", true],
  ] as const)("%s (job removed: %s) is read once", async (phase, jobGone) => {
    const row: JobSummary = { ...job, phase, jobGone };
    const fetchMock = detailFor(row);
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignCard, { job: row });
    await vi.advanceTimersByTimeAsync(RELOAD_MS * 5);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  test("a campaign still going keeps polling", async () => {
    const fetchMock = detailFor(job);
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(RELOAD_MS * 3);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  test("a campaign that finishes while shown is read once more, then left", async () => {
    const fetchMock = detailFor(job);
    vi.stubGlobal("fetch", fetchMock);
    const { rerender } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await rerender({ job: { ...job, phase: "Succeeded" } });
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(2); // its final state
    await vi.advanceTimersByTimeAsync(RELOAD_MS * 5);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  test("a finished campaign's Job being removed is read once more", async () => {
    // The record the API then serves is a different document from the
    // live Job's (the per-index states went with the Job).
    const done: JobSummary = { ...job, phase: "Succeeded" };
    const fetchMock = detailFor(done);
    vi.stubGlobal("fetch", fetchMock);
    const { rerender } = render(CampaignCard, { job: done });
    await vi.advanceTimersByTimeAsync(0);
    await rerender({ job: { ...done, jobGone: true } });
    await vi.advanceTimersByTimeAsync(RELOAD_MS * 5);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  test("a finished campaign whose one read failed tries again until it lands", async () => {
    const done: JobSummary = { ...job, phase: "Succeeded" };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("", { status: 503 }))
      .mockResolvedValue(
        jsonResponse({ ...detail0, ...done, failures: [], volumes: [] }),
      );
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignCard, { job: done });
    await vi.advanceTimersByTimeAsync(0);
    expect(
      screen.getByText(/Can't reach the campaign service/),
    ).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(RELOAD_MS * 2); // the backed-off retry
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/Can't reach the campaign service/)).toBeNull();
    await vi.advanceTimersByTimeAsync(RELOAD_MS * 10);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

// A page of finished campaigns made one detail request per card the moment
// it opened -- each reading the campaign's volume list, its pods and up to a
// hundred progress files -- folded or not, on screen or not (2026-09-23
// audit). A finished card now reads its detail once it is on screen or
// opened; a running one polls as before.
describe("a finished card reads its detail only once someone can see it", () => {
  /** An IntersectionObserver the test decides the visibility for. */
  class FakeObserver {
    static all: FakeObserver[] = [];
    observed: Element[] = [];
    disconnected = false;
    constructor(private readonly callback: IntersectionObserverCallback) {
      FakeObserver.all.push(this);
    }
    observe(el: Element): void {
      this.observed.push(el);
    }
    disconnect(): void {
      this.disconnected = true;
    }
    unobserve(): void {}
    show(): void {
      const entries = this.observed.map(
        (target) =>
          ({ isIntersecting: true, target }) as IntersectionObserverEntry,
      );
      this.callback(entries, this as unknown as IntersectionObserver);
    }
  }

  beforeEach(() => {
    vi.useFakeTimers();
    storage = stubStorage();
    FakeObserver.all = [];
    vi.stubGlobal("IntersectionObserver", FakeObserver);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const done: JobSummary = { ...job, phase: "Succeeded" };

  function detailFor(row: JobSummary) {
    return vi.fn(async () =>
      jsonResponse({ ...detail0, ...row, failures: [], volumes: [] }),
    );
  }

  test("folded and off screen, it asks nothing", async () => {
    const fetchMock = detailFor(done);
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignCard, { job: done });
    await vi.advanceTimersByTimeAsync(RELOAD_MS * 5);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("scrolled into view, it reads its detail once", async () => {
    const fetchMock = detailFor(done);
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignCard, { job: done });
    await vi.advanceTimersByTimeAsync(0);
    FakeObserver.all[0]?.show();
    await vi.advanceTimersByTimeAsync(RELOAD_MS * 5);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(FakeObserver.all[0]?.disconnected).toBe(true);
  });

  test("opened without being scrolled to, it reads its detail", async () => {
    const fetchMock = detailFor(done);
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignCard, { job: done });
    await vi.advanceTimersByTimeAsync(0);
    await expand();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // Folding and opening it again is not news.
    await expand();
    await expand();
    await vi.advanceTimersByTimeAsync(RELOAD_MS * 3);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  test("a card left open last time reads its detail at once", async () => {
    storage.set(`htrflow.card.${done.namespace}/${done.name}`, "open");
    const fetchMock = detailFor(done);
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignCard, { job: done });
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  test("a running campaign polls whether or not it is on screen", async () => {
    const fetchMock = detailFor(job);
    vi.stubGlobal("fetch", fetchMock);
    render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(RELOAD_MS * 2);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

// "Partially succeeded" and "partially failed" wore the same amber, on the
// chip and on the card's left accent, and a reader scanning the list could
// not tell a campaign that lost a few pages from one that lost whole volumes
// (a maintainer request). Each now mixes the amber with where it ended:
// green for every volume finished, red for volumes lost. Which mix each
// state asks for is the phase-chip table's; this checks what each mix is
// drawn with.
describe("the two partial states are told apart by colour", () => {
  test("each mix is drawn with its own tokens: a hard split on the chip, a gradient on the accent", () => {
    for (const [mix, health, token] of [
      ["success", "partly-succeeded", "var(--success)"],
      ["destructive", "partly-failed", "var(--destructive)"],
    ]) {
      expect(cssOf(`.chip.phase[data-mix="${mix}"]`).get("--mix-to")).toBe(
        token,
      );
      expect(cssOf(`.campaign[data-health="${health}"]`).get("--mix-to")).toBe(
        token,
      );
    }
    // The chip: amber and the mix meet at one point, never a blend under
    // the word. The accent has no text on it, so it may blend.
    expect(cssOf(".chip.phase[data-mix]").get("background")).toContain(
      "linear-gradient(90deg, var(--warning) 50%, var(--mix-to) 50%)",
    );
    expect(
      cssOf('.campaign[data-health^="partly-"]').get("background"),
    ).toContain("linear-gradient(to bottom, var(--warning), var(--mix-to))");
  });
});

// A campaign of one volume was two rows of totals over one identical row:
// the same two fractions said twice (the product owner, 2026-09-16).
describe("a campaign of one volume", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const one: JobSummary = {
    ...job,
    counts: { total: 1, active: 0, done: 1, failed: 0 },
    phase: "Succeeded",
  };
  const only = {
    ...volumeDone,
    progress: {
      done: 3,
      total: 3,
      failed: 0,
      lastPage: "0003",
      stage: "done",
      updatedAt: "2026-09-14T07:00:00Z",
      ageSeconds: null,
      lastError: null,
      errors: 0,
      viewerPublished: true,
    },
  };

  async function card(row: JobSummary, body: Record<string, unknown>) {
    cleanup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, ...row, failures: [], ...body }),
      ),
    );
    const { container } = render(CampaignCard, { job: row });
    await vi.advanceTimersByTimeAsync(0);
    return container;
  }

  function labels(container: HTMLElement): (string | null)[] {
    return [...container.querySelectorAll(".row.totals .c-label")].map(
      (el) => el.textContent,
    );
  }

  test("drops both totals: its own row is the total", async () => {
    const container = await card(one, { volumes: [only], latest: only });
    expect(labels(container)).toEqual([]);
    // The numbers are still on the card, once.
    expect(
      container.querySelector(".row.latest .c-fraction"),
    ).toHaveTextContent("3 / 3");
  });

  test("...open as well as folded", async () => {
    const container = await card(one, { volumes: [only], latest: only });
    await expand();
    expect(labels(container)).toEqual([]);
    expect(
      container.querySelector(".row.volume .c-fraction"),
    ).toHaveTextContent("3 / 3");
  });

  test("but keeps them while there is no row to carry them", async () => {
    // The detail has not landed: a card with no numbers at all would be
    // worse than a total of one.
    const container = await card(one, { volumes: [], latest: null });
    expect(labels(container)).toEqual(["volumes", "pages"]);
  });

  test("a campaign of more than one keeps both", async () => {
    const many: JobSummary = {
      ...job,
      counts: { total: 3, active: 0, done: 3, failed: 0 },
    };
    const container = await card(many, { volumes: [only], latest: only });
    expect(labels(container)).toEqual(["volumes", "pages"]);
  });
});

// Two things the live card showed (the product owner, 2026-09-16): the
// label track took all the free width, so "volumes" sat at the far left with
// its bar 550px away and nothing in between; and a volume row carried no bar
// at all, which on a single-volume card meant no bar anywhere.
describe("the bar is the track that stretches, and every volume has one", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  function progress(over: Record<string, unknown> = {}) {
    return {
      done: 2,
      total: 3,
      failed: 0,
      lastPage: "0003",
      stage: "done",
      updatedAt: "2026-09-14T07:00:00Z",
      ageSeconds: null,
      lastError: null,
      errors: 0,
      viewerPublished: true,
      ...over,
    };
  }

  async function card(volumes: unknown[], latest: unknown = null) {
    cleanup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [], volumes, latest }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    return container;
  }

  test("the label takes the free width; the bar, fraction and pill are fixed", () => {
    // The words take the left and the three fixed things pack against the
    // right in the order a reader wants them: the bar, the fraction it
    // draws, and the state it ended in.
    expect(cssOf(".row").get("grid-template-columns")).toBe(
      "minmax(6rem, 1fr) var(--icons) var(--bar) var(--fraction) var(--pill)",
    );
    expect(cssOf(".campaign").get("--bar")).toBe("6rem");
  });

  test("a long volume id clips rather than widening its track", async () => {
    const long = {
      ...volumeDone,
      id: "R0001203-a-very-long-identifier-indeed",
      progress: progress(),
    };
    const container = await card([long], long);
    const name = container.querySelector(".vid-name") as HTMLElement;
    expect(name).toHaveAttribute("title", expect.stringContaining(long.id));
    const clip = cssOf(".vid-name");
    expect(clip.get("white-space")).toBe("nowrap");
    expect(clip.get("overflow")).toBe("hidden");
    expect(clip.get("text-overflow")).toBe("ellipsis");
  });

  test.each([
    ["active" as const, 0, "running"],
    ["done" as const, 0, "done"],
    ["done" as const, 1, "lost"],
    ["failed" as const, 0, "failed"],
  ])(
    "a %s volume carries a bar in its own colour",
    async (state, failed, mode) => {
      const v = { ...volumeDone, state, progress: progress({ failed }) };
      const container = await card([v], v);
      const fill = container.querySelector(
        ".row.volume .c-bar .fill",
      ) as HTMLElement;
      expect(fill).not.toBeNull();
      expect(fill).toHaveClass(mode);
      expect(fill).toHaveStyle({ width: "66.7%" });
    },
  );

  test("only a running volume's bar sheens", async () => {
    const active = {
      ...volumeDone,
      state: "active",
      progress: progress({ stage: "stream", ageSeconds: 12 }),
    };
    const container = await card([active], active);
    expect(container.querySelector(".row.volume .fill")).toHaveClass("running");
    const done = { ...volumeDone, progress: progress() };
    const second = await card([done], done);
    expect(second.querySelector(".row.volume .fill")).not.toHaveClass(
      "running",
    );
  });

  test.each([["pending" as const], ["unknown" as const]])(
    "a %s volume leaves the bar track empty",
    async (state) => {
      const v = { ...volumeDone, state, progress: null };
      const container = await card([v], v);
      const track = container.querySelector(
        ".row.volume .c-bar",
      ) as HTMLElement;
      expect(track).not.toBeNull();
      expect(track.querySelector(".bar")).toBeNull();
    },
  );

  test("a single-volume card still has a bar, now that its row carries one", async () => {
    const one: JobSummary = {
      ...job,
      counts: { total: 1, active: 0, done: 1, failed: 0 },
      phase: "Succeeded",
    };
    const only = { ...volumeDone, progress: progress({ done: 3, total: 3 }) };
    cleanup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          ...one,
          failures: [],
          volumes: [only],
          latest: only,
        }),
      ),
    );
    const { container } = render(CampaignCard, { job: one });
    await vi.advanceTimersByTimeAsync(0);
    expect(container.querySelectorAll(".row.totals")).toHaveLength(0);
    expect(container.querySelector(".row.latest .fill")).toHaveClass("done");
  });
});

// "page 0004: HTTP 400  log ... seems misplaced" (the product owner,
// 2026-09-16): a page error belongs to one volume — it is that volume's
// own progress.lastError — so it belongs under that volume's row, not in a
// line about the campaign.
describe("where a page error is said", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const lastError = {
    page: "0004",
    error: "HTTP 400",
    volume: "vol0",
    logUrl: "https://pub/status/logs/demo-v1/vol0.txt",
  };

  async function card(body: Record<string, unknown>) {
    cleanup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ ...detail0, failures: [], pagesFailed: 1, ...body }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    return container;
  }

  test("under the volume it happened in, when that volume is on screen", async () => {
    const container = await card({
      volumes: [volumeDone],
      latest: volumeDone,
      lastError,
    });
    const note = container.querySelector(".row-note") as HTMLElement;
    expect(note).toHaveTextContent("page 0004: HTTP 400");
    expect(note).not.toHaveAttribute("title");
    expect(within(note).getByRole("link", { name: "log" })).toHaveAttribute(
      "href",
      expect.stringContaining(encodeURIComponent(lastError.logUrl)),
    );
    // ...and not in the campaign's own line, which has nothing else to say.
    expect(container.querySelector(".problems")).toBeNull();
  });

  test("the note follows its own row, open as well as folded", async () => {
    const other = { ...volumeDone, index: 1, id: "vol1" };
    const container = await card({
      volumes: [other, volumeDone],
      latest: other,
      lastError,
    });
    // Folded on vol1: vol0 is not on screen, so the campaign line keeps it.
    expect(container.querySelector(".row-note")).toBeNull();
    expect(container.querySelector(".problems-text")).toHaveTextContent(
      "page 0004: HTTP 400",
    );
    await expand();
    // Open: vol0 is a row, and its note sits under it.
    const rows = [...container.querySelectorAll(".row.volume, .row-note")];
    const noteAt = rows.findIndex((r) => r.className.includes("row-note"));
    expect(noteAt).toBeGreaterThan(0);
    expect(rows[noteAt - 1]).toHaveTextContent("vol0");
    expect(container.querySelector(".problems")).toBeNull();
  });

  test("a volume that is nowhere on the card keeps its error in the campaign line", async () => {
    const container = await card({
      volumes: [{ ...volumeDone, index: 9, id: "vol9" }],
      latest: null,
      lastError,
    });
    await expand();
    expect(container.querySelector(".row-note")).toBeNull();
    expect(container.querySelector(".problems-text")).toHaveTextContent(
      "page 0004: HTTP 400",
    );
  });

  test("the campaign line still carries a warm-up failure and a failed volume", async () => {
    const cold: JobSummary = {
      ...job,
      warmup: {
        phase: "failed",
        reason: { stage: "warmup", permanent: true, error: "bad model id" },
      },
    };
    cleanup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...detail0,
          ...cold,
          failures: [volumeFailed],
          volumes: [volumeDone],
          latest: volumeDone,
          pagesFailed: 1,
          lastError,
        }),
      ),
    );
    const { container } = render(CampaignCard, { job: cold });
    await vi.advanceTimersByTimeAsync(0);
    const line = container.querySelector(".problems-text") as HTMLElement;
    expect(line).toHaveTextContent("warm-up:");
    expect(line).toHaveTextContent("vol1:");
    // The page error is under its own row instead.
    expect(line).not.toHaveTextContent("HTTP 400");
    expect(container.querySelector(".row-note")).toHaveTextContent("HTTP 400");
  });
});

// On the live phone the totals rows drew their bar and the volume rows drew
// none (the product owner, 2026-09-16): line 2's fixed tracks are wider than
// a 390px card, and a squeezed grid took the width back from the only cell
// whose content has none of its own — the bar.
describe("the bar survives a phone's width", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubStorage();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  test("the phone template has a bar area, with a floor under it", () => {
    const row = cssOf(".row", PHONE);
    const grid = areas(row.get("grid-template-areas"));
    // The bar's area is a track that may shrink but never to nothing...
    const track = grid[1]?.indexOf("bar") ?? -1;
    expect(track).toBeGreaterThan(-1);
    expect(tracks(row.get("grid-template-columns"))[track]).toBe(
      "minmax(2.5rem, var(--bar))",
    );
    // ...its cell cannot be squeezed away either...
    const cell = cssOf(".c-bar", PHONE);
    expect(cell.get("grid-area")).toBe("bar");
    expect(cell.get("min-width")).toBe("2.5rem");
    // ...and the lost line still follows it, under the bar.
    expect(grid[2]?.slice(track)).toEqual(["lost", "lost", "lost"]);
  });

  test("a volume row and a totals row ask for the same bar", async () => {
    // Same markup, same cell, same rules: the fold cannot give one a bar and
    // the other none.
    cleanup();
    const v = {
      ...volumeDone,
      progress: {
        done: 2,
        total: 3,
        failed: 1,
        lastPage: "0003",
        stage: "done",
        updatedAt: "2026-09-14T07:00:00Z",
        ageSeconds: null,
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
          failures: [],
          volumes: [v],
          latest: v,
          pagesDone: 2,
          pagesTotal: 3,
        }),
      ),
    );
    const { container } = render(CampaignCard, { job });
    await vi.advanceTimersByTimeAsync(0);
    // Two totals rows and the folded volume row: three bars, one shape.
    expect(container.querySelectorAll(".row .c-bar .bar")).toHaveLength(3);
  });
});
