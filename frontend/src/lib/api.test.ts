import { afterEach, describe, expect, test, vi } from "vitest";
import {
  ApiUnreachable,
  clockTime,
  fetchJob,
  fetchJobs,
  isHttpUrl,
  isResultUrl,
  jobDetailSchema,
  jobSummarySchema,
  moreReaped,
  REAPED_MAX,
  REAPED_PAGE,
  reapedHidden,
  sameDay,
  shortDate,
  volumeStateSchema,
  warmupSchema,
} from "./api.js";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const summary = {
  namespace: "htr-test",
  name: "kyrk",
  pipeline: "demo-v1",
  phase: "Running",
  counts: { total: 7, active: 1, done: 4, failed: 1 },
  suspended: false,
  createdAt: "2026-01-01T00:00:00Z",
  // Both defaulted by the schema when an older API leaves them out; the
  // fixture carries them because the parsed row always does (B76).
  finishedAt: null,
  resultsBase: "https://results.example.org/htr-test/demo-v1",
  warmup: { phase: "succeeded" },
  jobGone: false,
};

const volume = {
  index: 0,
  id: "vol0",
  state: "done",
  manifestUrl:
    "https://results.example.org/htr-test/demo-v1/vol0/manifest.json",
  iiifUrl: "https://results.example.org/htr-test/demo-v1/vol0/iiif.json",
  altoPrefix: "https://results.example.org/htr-test/demo-v1/vol0/alto/",
  logUrl: "https://results.example.org/status/logs/demo-v1/vol0.txt",
  sourceUrl: "https://iiif.example.org/vol0/manifest",
  progress: null,
};

const pipeline = {
  pipelineSteps: ["Segmentation"],
  pipelineYaml: "steps:\n",
  latest: null,
  pagesDone: 0,
  pagesTotal: 0,
  pagesCoverage: { counted: 0, of: 0 },
  pagesFailed: 0,
  errors: 0,
  lastError: null,
};

describe("fetchJobs", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.API_BASE;
  });

  test("parses the list and hits the resolved API base with no-store", async () => {
    const fetchMock = vi.fn(async () => jsonResponse([summary]));
    vi.stubGlobal("fetch", fetchMock);
    const list = await fetchJobs();
    expect(list).toEqual({ jobs: [summary], unreadable: 0, reapedTotal: 0 });
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/jobs?reaped=${REAPED_PAGE}`,
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  // The API carries every live Job but only the newest campaigns whose Jobs
  // are gone, and says how many of those there are in all (2026-09-23
  // audit): the page asks for more by count.
  test("asks for as many reaped campaigns as it is told, and reads their total", async () => {
    const gone = { ...summary, name: "gamla", jobGone: true };
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify([summary, gone]), {
          headers: {
            "content-type": "application/json",
            "x-reaped-total": "57",
          },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const list = await fetchJobs(undefined, 40);
    expect(list.reapedTotal).toBe(57);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/jobs?reaped=40",
      expect.anything(),
    );
  });

  test("an API that sends no total has no more to offer than it sent", async () => {
    const gone = { ...summary, name: "gamla", jobGone: true };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([summary, gone])),
    );
    expect((await fetchJobs()).reapedTotal).toBe(1);
  });

  test("honours window.API_BASE, resolved per call", async () => {
    window.API_BASE = "http://elsewhere/api/v1";
    const fetchMock = vi.fn(async () => jsonResponse([summary]));
    vi.stubGlobal("fetch", fetchMock);
    await fetchJobs();
    expect(fetchMock).toHaveBeenCalledWith(
      `http://elsewhere/api/v1/jobs?reaped=${REAPED_PAGE}`,
      expect.anything(),
    );
  });

  test("a non-2xx response becomes ApiUnreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse("gone", 503)),
    );
    await expect(fetchJobs()).rejects.toThrow(ApiUnreachable);
    await expect(fetchJobs()).rejects.toThrow("HTTP 503");
  });

  test("a network error becomes ApiUnreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    await expect(fetchJobs()).rejects.toThrow(ApiUnreachable);
    await expect(fetchJobs()).rejects.toThrow("Failed to fetch");
  });

  test("a body that is not a list fails hard, not silently — and is not ApiUnreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ jobs: [summary] })),
    );
    await expect(fetchJobs()).rejects.toThrow();
    await expect(fetchJobs()).rejects.not.toBeInstanceOf(ApiUnreachable);
  });

  test("one malformed row is left out and counted; the others still render (B32)", async () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => {});
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse([summary, { ...summary, name: "broken", phase: "Bogus" }]),
      ),
    );
    expect(await fetchJobs()).toEqual({
      jobs: [summary],
      unreadable: 1,
      reapedTotal: 0,
    });
    expect(consoleError).toHaveBeenCalledTimes(1); // the bug stays visible to the operator
    consoleError.mockRestore();
  });

  test("a list whose every row is malformed still fails hard", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([{ ...summary, phase: "Bogus" }])),
    );
    await expect(fetchJobs()).rejects.toThrow();
    vi.restoreAllMocks();
  });
});

describe("fetchJob", () => {
  afterEach(() => vi.unstubAllGlobals());

  test("builds the paged URL and parses the detail", async () => {
    const detail = { ...summary, ...pipeline, failures: [], volumes: [volume] };
    const fetchMock = vi.fn(async () => jsonResponse(detail));
    vi.stubGlobal("fetch", fetchMock);
    const result = await fetchJob("htr-test", "kyrk", 5, 50);
    expect(result.volumes).toEqual([volume]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/jobs/htr-test/kyrk?offset=5&limit=50",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  test("defaults to offset=0, limit=200", async () => {
    const detail = { ...summary, ...pipeline, failures: [], volumes: [] };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    await fetchJob("htr-test", "kyrk");
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/jobs/htr-test/kyrk?offset=0&limit=200",
      expect.anything(),
    );
  });

  test("namespace and name are URL-encoded", async () => {
    const detail = { ...summary, ...pipeline, failures: [], volumes: [] };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    await fetchJob("a/b", "c d");
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/jobs/a%2Fb/c%20d?offset=0&limit=200",
      expect.anything(),
    );
  });

  test("a 404 (job not found) becomes ApiUnreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "job not found" }, 404)),
    );
    await expect(fetchJob("htr-test", "nope")).rejects.toThrow(ApiUnreachable);
  });
});

describe("schemas", () => {
  test("a detail without the pipeline fields is refused", () => {
    expect(() =>
      jobDetailSchema.parse({ ...summary, failures: [], volumes: [] }),
    ).toThrow();
  });

  test("an unknown phase is rejected, not coerced to a neutral value", () => {
    expect(() =>
      jobSummarySchema.parse({ ...summary, phase: "Bogus" }),
    ).toThrow();
  });

  test("a reason that is still the old bare string is rejected", () => {
    expect(() =>
      jobDetailSchema.parse({
        ...summary,
        ...pipeline,
        failures: [],
        volumes: [{ ...volume, reason: "exit 1" }],
      }),
    ).toThrow();
  });

  test("a volume row without the progress field is refused", () => {
    const { progress: _dropped, ...noProgress } = volume;
    expect(() =>
      jobDetailSchema.parse({
        ...summary,
        ...pipeline,
        failures: [],
        volumes: [noProgress],
      }),
    ).toThrow();
  });

  test("warmup is required on a job row", () => {
    expect(() =>
      jobSummarySchema.parse({ ...summary, warmup: undefined }),
    ).toThrow();
  });

  test("an unknown warmup phase is rejected", () => {
    expect(() => warmupSchema.parse({ phase: "bogus" })).toThrow();
  });
});

describe("isHttpUrl", () => {
  test("accepts absolute http(s) URLs only", () => {
    expect(isHttpUrl("http://x/y")).toBe(true);
    expect(isHttpUrl("https://x/y?z=1")).toBe(true);
    expect(isHttpUrl("HTTPS://X/")).toBe(true);
    expect(isHttpUrl("javascript:alert(1)")).toBe(false);
    expect(isHttpUrl("data:text/html,hi")).toBe(false);
    expect(isHttpUrl("ftp://x/y")).toBe(false);
    expect(isHttpUrl("/relative/path")).toBe(false);
    expect(isHttpUrl("")).toBe(false);
    expect(isHttpUrl(" http://x")).toBe(false);
  });
});

describe("shortDate", () => {
  test("formats an ISO timestamp", () => {
    expect(shortDate("2026-08-25T14:32:00Z", "UTC")).toBe("25 Aug, 14:32");
  });
  test("null and junk stay null", () => {
    expect(shortDate(null)).toBeNull();
    expect(shortDate("not-a-date")).toBeNull();
  });
});

describe("an abandoned request is actually abandoned", () => {
  afterEach(() => vi.unstubAllGlobals());

  test("fetchJobs hands its signal to fetch", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
      jsonResponse([]),
    );
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await fetchJobs(controller.signal);
    expect(fetchMock.mock.calls[0]?.[1]?.signal).toBe(controller.signal);
  });

  test("fetchJob hands its signal to fetch", async () => {
    const body = { ...summary, ...pipeline, failures: [], volumes: [] };
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
      jsonResponse(body),
    );
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await fetchJob("htr-test", "kyrk", 0, 200, controller.signal);
    expect(fetchMock.mock.calls[0]?.[1]?.signal).toBe(controller.signal);
  });

  test("an aborted fetch reads as the API being unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new DOMException("aborted", "AbortError");
      }),
    );
    const controller = new AbortController();
    controller.abort();
    await expect(fetchJobs(controller.signal)).rejects.toBeInstanceOf(
      ApiUnreachable,
    );
  });
});

describe("a URL field has to be a URL", () => {
  // Every one of these becomes an href or a fetch target. They are our own
  // API's, so a value that is not an absolute http(s) URL is a bug on our
  // side -- and the schema is where this page says so (2026-09-14 audit).
  test("a campaign row with a resultsBase that is not a URL is unreadable", () => {
    expect(
      jobSummarySchema.safeParse({ ...summary, resultsBase: "/results" })
        .success,
    ).toBe(false);
  });

  test.each(["manifestUrl", "iiifUrl", "altoPrefix", "logUrl"])(
    "a volume row with a %s that is not a URL is unreadable",
    (field) => {
      const detail = {
        ...summary,
        ...pipeline,
        failures: [],
        volumes: [{ ...volume, [field]: "javascript:alert(1)" }],
      };
      expect(jobDetailSchema.safeParse(detail).success).toBe(false);
    },
  );

  // sourceUrl alone is not built by the API: it is a line of a campaign's
  // volumes.txt, which people edit, and the API's check and the browser's
  // URL parser need not agree on every string. One the page cannot use is
  // no link -- never a card that fails to parse, and so re-polls for ever
  // (2026-09-23 audit).
  test.each([
    "images:x",
    "javascript:alert(1)",
    "https://example.org:99999/m",
    "https://exa%mple.org/m",
    "https://ex<ample.org/m",
    42,
  ])(
    "a sourceUrl the page cannot use (%s) is no link, not a lost card",
    (bad) => {
      const detail = {
        ...summary,
        ...pipeline,
        failures: [{ ...volume, sourceUrl: bad }],
        latest: { ...volume, sourceUrl: bad },
        volumes: [volume, { ...volume, index: 1, id: "vol1", sourceUrl: bad }],
      };
      const parsed = jobDetailSchema.parse(detail);
      expect(parsed.volumes.map((v) => v.sourceUrl)).toEqual([
        volume.sourceUrl,
        null,
      ]);
      expect(parsed.failures[0]?.sourceUrl).toBeNull();
      expect(parsed.latest?.sourceUrl).toBeNull();
    },
  );

  test("sourceUrl may be null", () => {
    const detail = {
      ...summary,
      ...pipeline,
      failures: [],
      volumes: [{ ...volume, sourceUrl: null }],
    };
    expect(jobDetailSchema.parse(detail).volumes[0]?.sourceUrl).toBeNull();
  });

  test("the campaign notice's log link is a URL too", () => {
    const detail = {
      ...summary,
      ...pipeline,
      failures: [],
      volumes: [],
      lastError: {
        page: "0044",
        error: "boom",
        volume: "vol0",
        logUrl: "not a url",
      },
    };
    expect(jobDetailSchema.safeParse(detail).success).toBe(false);
  });
});

describe("isResultUrl", () => {
  const base = "https://results.example.org/bucket";

  test("a URL under the configured results base is allowed", () => {
    expect(isResultUrl(`${base}/status/logs/demo/v1.txt`, base)).toBe(true);
    expect(isResultUrl(`${base}/htr-test/demo/vol0/manifest.json`, base)).toBe(
      true,
    );
  });

  test("a URL that walks back out of the base is refused", () => {
    // `/bucket/../evil.txt` starts with the base as text and is not under
    // it at all (2026-09-14 review).
    expect(isResultUrl(`${base}/../evil.txt`, base)).toBe(false);
    expect(isResultUrl(`${base}/x/../../evil.txt`, base)).toBe(false);
    expect(isResultUrl(`${base}/x/../y.txt`, base)).toBe(true);
  });

  test("an escaped separator below the base is refused", () => {
    // The URL parser resolves `..` and `%2e%2e`, but not a `..` that is
    // only a dot segment once `%2F` or `%5C` is decoded — which a proxy
    // that decodes before it resolves would walk out of the base with. No
    // result URL carries one: volume ids admit neither separator.
    expect(isResultUrl(`${base}/..%2Fother/run.log`, base)).toBe(false);
    expect(isResultUrl(`${base}/x/..%2f..%2fother.txt`, base)).toBe(false);
    expect(isResultUrl(`${base}/..%5Cother/run.log`, base)).toBe(false);
    expect(isResultUrl(`${base}/%2e%2e%2fother/run.log`, base)).toBe(false);
    // Only the path: a query may carry one (a signed URL's credential does).
    expect(isResultUrl(`${base}/x/run.log?cred=a%2Fb`, base)).toBe(true);
  });

  test("the same URL written differently is still the same URL", () => {
    expect(isResultUrl(`${base}/a%2Db.txt`, base)).toBe(true);
    expect(
      isResultUrl(`${base}/x.txt`, "HTTPS://results.example.org/bucket"),
    ).toBe(true);
  });

  test("a URL anywhere else is not, however absolute it is", () => {
    expect(isResultUrl("https://evil.example.org/log.txt", base)).toBe(false);
    // The prefix has to end at a path boundary, or a lookalike host passes.
    expect(isResultUrl(`${base}.evil.org/log.txt`, base)).toBe(false);
    expect(isResultUrl("javascript:alert(1)", base)).toBe(false);
  });

  test("a trailing slash on the base changes nothing", () => {
    expect(isResultUrl(`${base}/x.txt`, `${base}/`)).toBe(true);
  });

  test("an unset base accepts any absolute http(s) URL, as before", () => {
    expect(isResultUrl("https://anywhere.example.org/x.txt", "")).toBe(true);
    expect(isResultUrl("javascript:alert(1)", "")).toBe(false);
  });
});

describe("a volume whose state nobody recorded", () => {
  test("a state nobody defined is still refused", () => {
    expect(volumeStateSchema.safeParse("probably-fine").success).toBe(false);
  });
});

describe("the compact date range on a card", () => {
  test("clockTime is the clock half of shortDate", () => {
    expect(clockTime("2026-09-14T10:56:00Z", "UTC")).toBe("10:56");
    expect(clockTime("not-a-date")).toBeNull();
  });

  test("sameDay says whether the end needs its date repeated", () => {
    expect(sameDay("2026-09-14T10:56:00Z", "2026-09-14T11:00:00Z", "UTC")).toBe(
      true,
    );
    expect(sameDay("2026-09-14T23:56:00Z", "2026-09-15T00:10:00Z", "UTC")).toBe(
      false,
    );
    expect(sameDay("2025-09-14T10:00:00Z", "2026-09-14T10:00:00Z", "UTC")).toBe(
      false,
    );
    expect(sameDay("nope", "2026-09-14T10:00:00Z", "UTC")).toBe(false);
  });
});

// Asked for past the API's own maximum, the list 422'd, the page read that
// as the API being unreachable and stopped updating (2026-09-23 review).
describe("asking for older campaigns stops at what the API allows", () => {
  test("each ask is one page more, never past the maximum", () => {
    expect(moreReaped(REAPED_PAGE)).toBe(REAPED_PAGE * 2);
    expect(moreReaped(REAPED_MAX - 5)).toBe(REAPED_MAX);
    expect(moreReaped(REAPED_MAX)).toBe(REAPED_MAX);
  });

  test("nothing is left to offer once the maximum is shown", () => {
    expect(reapedHidden(25, REAPED_PAGE)).toBe(5);
    expect(reapedHidden(25, 40)).toBe(0);
    expect(reapedHidden(REAPED_MAX + 500, REAPED_MAX)).toBe(0);
    expect(reapedHidden(REAPED_MAX + 500, REAPED_MAX - 20)).toBe(20);
  });
});
