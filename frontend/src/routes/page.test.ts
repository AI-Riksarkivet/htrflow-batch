import { cleanup, fireEvent, render, screen } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { RELOAD_MS } from "$lib/config.js";
import { cssOf, cssRules } from "$lib/fixtures/css.js";
import CampaignsPage from "./+page.svelte";
import pageSource from "./+page.svelte?raw";

const pageRules = cssRules(pageSource);

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
  pagesCoverage: { counted: 0, of: 0 },
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
      "/api/v1/jobs?reaped=20",
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(screen.getByText("kyrk")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  // The read API serves its own namespace unless told otherwise, and a
  // prefix every card carries says nothing. It is said only when the list
  // spans namespaces, which is when it tells two campaigns apart.
  test("one namespace: bare names; two: namespace/name, and one card each", async () => {
    const elsewhere = { ...job, namespace: "htr-other" };
    vi.stubGlobal("fetch", routedFetch([job, elsewhere]));
    const { container } = render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);

    const names = [...container.querySelectorAll(".camp-name")].map(
      (el) => el.textContent,
    );
    // Same name, two namespaces: two cards, since the key is namespace/name.
    expect(names).toEqual(["htr-test/kyrk", "htr-other/kyrk"]);
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
      if (url === "/api/v1/jobs?reaped=20") {
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
    expect(screen.getByText("kyrk")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(RELOAD_MS);
    expect(listCalls).toBe(2);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Can't reach the campaign service right now (HTTP 503). Showing the " +
        "list we last received. Retrying every 60 seconds.",
    );
    expect(screen.getByText("kyrk")).toBeInTheDocument();
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
    expect(names).toEqual(["kyrk", "gamla"]);
    expect(container.querySelectorAll("section.campaign")).toHaveLength(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  // Records have no TTL, so the API sends only the newest campaigns whose
  // Jobs are gone and says how many there are (2026-09-23 audit). The rest
  // are one click away, a page at a time.
  test("older reaped campaigns wait behind a button, a page at a time", async () => {
    const gone = (i: number) => ({
      ...job,
      name: `gamla${i}`,
      phase: "Succeeded",
      jobGone: true,
    });
    const asked: string[] = [];
    const fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith("/version"))
        return jsonResponse({ version: "v", web: "w" });
      if (url.includes("/jobs/")) return jsonResponse(detail);
      asked.push(url);
      const n = Number(new URL(url, "http://x").searchParams.get("reaped"));
      const rows = [
        job,
        ...Array.from({ length: Math.min(n, 25) }, (_, i) => gone(i)),
      ];
      return new Response(JSON.stringify(rows), {
        headers: { "content-type": "application/json", "x-reaped-total": "25" },
      });
    }) as unknown as typeof fetch;
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);
    expect(container.querySelectorAll("section.campaign")).toHaveLength(21);

    const more = screen.getByRole("button", { name: /5 older campaigns/ });
    await fireEvent.click(more);
    await vi.advanceTimersByTimeAsync(0);
    expect(asked.at(-1)).toBe("/api/v1/jobs?reaped=40");
    expect(container.querySelectorAll("section.campaign")).toHaveLength(26);
    expect(
      screen.queryByRole("button", { name: /older campaigns/ }),
    ).toBeNull();
  });

  test("with every reaped campaign shown there is no button", async () => {
    vi.stubGlobal("fetch", routedFetch([job]));
    render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);
    expect(
      screen.queryByRole("button", { name: /older campaigns/ }),
    ).toBeNull();
  });

  // One row the page cannot read hides that row, not the list, and the
  // banner says how many are hidden (B32).
  test("rows it cannot read are counted in a banner over the ones it can", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const bad = (name: string) => ({ ...job, name, phase: "Bogus" });
    vi.stubGlobal("fetch", routedFetch([job, bad("x"), bad("y")]));
    const { container } = render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "2 campaigns could not be read and are not shown.",
    );
    expect(container.querySelectorAll("section.campaign")).toHaveLength(1);
    vi.restoreAllMocks();
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
  // polling for its own volume table (2026-09-14 audit). What a poll does
  // with a hidden tab or a slow answer is $lib/poll's own test; this is that
  // the page and its cards poll through it.
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
      "kyrk",
      "broken",
      "newer",
      "older",
      "queued",
    ]);
  });
});

// The list arrives in stages -- the list, the version, each card's detail,
// a poll a minute later -- and nothing a reader is looking at may move when
// a later stage lands (the repo owner: "things pop all over"). Measured in a
// browser by scripts/measure-shifts.mjs; these pin each cause it found.
describe("/ nothing moves as the page loads", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  // Unanswered, the version took no room; answered, it widened the header's
  // right half, which on a phone wrapped under the title and pushed the
  // whole list down a line (CLS 0.28 at 390px).
  test("the version holds its place in the header before it is read", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        url.toString().endsWith("/version")
          ? new Promise<Response>(() => {})
          : Promise.resolve(jsonResponse([job])),
      ) as unknown as typeof fetch,
    );
    const { container } = render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);
    const slot = container.querySelector(".header-right .version");
    expect(slot).not.toBeNull();
    expect(slot).toHaveTextContent("");
    expect(cssOf(pageRules, ".version").get("min-width")).toMatch(/rem$/);
    // Grows away from the icons beside it, never into them.
    expect(cssOf(pageRules, ".version").get("text-align")).toBe("right");
    expect(cssOf(pageRules, ".header-right").get("margin-left")).toBe("auto");
  });

  // Before the first answer there is nothing to say yet: no empty state, no
  // banner, and a "Loading…" that waits a moment before it shows, so a
  // quick answer replaces nothing a reader saw (it flashed for a few
  // hundred milliseconds on every load).
  test("before the first answer: no empty state, no banner, a loading line that waits", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => {})) as unknown as typeof fetch,
    );
    const { container } = render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.queryByText("No campaigns.")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    const loading = container.querySelector(".loading");
    expect(loading).toHaveTextContent("Loading…");
    expect(container.querySelector("main")).toHaveAttribute(
      "aria-busy",
      "true",
    );
    const css = cssOf(pageRules, ".loading");
    expect(css.get("animation")).toMatch(
      /\b\d+ms\b.*\bboth\b|\bboth\b.*\b\d+ms\b/,
    );
  });

  // The order is the first answer's; a poll updates each card where it is.
  // Re-sorting every answer moved a campaign that had just started from the
  // bottom of the list to the top, and every card in between down one.
  describe("the order a reader has", () => {
    const started = { ...job, name: "started", phase: "Queued" };
    const finishing = { ...job, name: "finishing" };

    function answers(...lists: unknown[][]): typeof fetch {
      let n = 0;
      return vi.fn(async (url: string) => {
        if (url.toString().endsWith("/version"))
          return jsonResponse({ version: "v", web: "w" });
        if (url.toString().includes("/jobs/")) return jsonResponse(detail);
        return jsonResponse(lists[Math.min(n++, lists.length - 1)]);
      }) as unknown as typeof fetch;
    }

    const names = (container: HTMLElement) =>
      [...container.querySelectorAll(".camp-name")].map((el) => el.textContent);

    function setHidden(hidden: boolean): void {
      Object.defineProperty(document, "hidden", {
        configurable: true,
        get: () => hidden,
      });
      document.dispatchEvent(new Event("visibilitychange"));
    }

    afterEach(() => setHidden(false));

    test("a poll moves no card, whatever changed in it", async () => {
      const later = [
        { ...started, phase: "Running" },
        {
          ...finishing,
          phase: "Succeeded",
          finishedAt: "2026-09-08T10:00:00Z",
        },
      ];
      vi.stubGlobal("fetch", answers([started, finishing], later));
      const { container } = render(CampaignsPage);
      await vi.advanceTimersByTimeAsync(0);
      expect(names(container)).toEqual(["finishing", "started"]);
      await vi.advanceTimersByTimeAsync(RELOAD_MS);
      expect(names(container)).toEqual(["finishing", "started"]);
      // The card itself says what changed.
      const cards = container.querySelectorAll("section.campaign");
      expect(cards[1]).toHaveTextContent("Running");
    });

    test("back from the background, the list is sorted afresh", async () => {
      const later = [
        { ...started, phase: "Running" },
        {
          ...finishing,
          phase: "Succeeded",
          finishedAt: "2026-09-08T10:00:00Z",
        },
      ];
      vi.stubGlobal("fetch", answers([started, finishing], later));
      const { container } = render(CampaignsPage);
      await vi.advanceTimersByTimeAsync(0);
      setHidden(true);
      await vi.advanceTimersByTimeAsync(RELOAD_MS);
      setHidden(false);
      await vi.advanceTimersByTimeAsync(0);
      expect(names(container)).toEqual(["started", "finishing"]);
    });
  });

  // A banner that appeared over a list already on screen pushed every card
  // down by its own height. Over a list, it floats at the foot of the
  // window; with no list yet, there is nothing to push, and it stays in the
  // page where the list would be.
  test("a banner over a list moves no card; with no list it sits in the page", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.toString().endsWith("/version"))
          return jsonResponse({ version: "v", web: "w" });
        if (url.toString().includes("/jobs/")) return jsonResponse(detail);
        return ++calls === 1 ? jsonResponse([job]) : jsonResponse("gone", 503);
      }) as unknown as typeof fetch,
    );
    render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(RELOAD_MS);
    expect(screen.getByRole("alert")).toHaveClass("floating");
    expect(cssOf(pageRules, ".banner.floating").get("position")).toBe("fixed");
    cleanup();

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse("gone", 503)) as unknown as typeof fetch,
    );
    render(CampaignsPage);
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByRole("alert")).not.toHaveClass("floating");
  });
});
