import { afterEach, describe, expect, test, vi } from "vitest";
import {
  ApiUnreachable,
  fetchJob,
  fetchJobs,
  isHttpUrl,
  jobDetailSchema,
  jobSummarySchema,
  shortDate,
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
  resultsBase: "https://results.example.org/htr-test/demo-v1",
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
};

describe("fetchJobs", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.API_BASE;
  });

  test("parses the list and hits the resolved API base with no-store", async () => {
    const fetchMock = vi.fn(async () => jsonResponse([summary]));
    vi.stubGlobal("fetch", fetchMock);
    const jobs = await fetchJobs();
    expect(jobs).toEqual([summary]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/jobs",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  test("honours window.API_BASE, resolved per call", async () => {
    window.API_BASE = "http://elsewhere/api/v1";
    const fetchMock = vi.fn(async () => jsonResponse([summary]));
    vi.stubGlobal("fetch", fetchMock);
    await fetchJobs();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://elsewhere/api/v1/jobs",
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

  test("a malformed body fails hard, not silently — and is not ApiUnreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([{ ...summary, phase: "Bogus" }])),
    );
    await expect(fetchJobs()).rejects.toThrow();
    await expect(fetchJobs()).rejects.not.toBeInstanceOf(ApiUnreachable);
  });
});

describe("fetchJob", () => {
  afterEach(() => vi.unstubAllGlobals());

  test("builds the paged URL and parses the detail", async () => {
    const detail = { ...summary, failures: [], volumes: [volume] };
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
    const detail = { ...summary, failures: [], volumes: [] };
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
    const detail = { ...summary, failures: [], volumes: [] };
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
  test("jobSummarySchema and jobDetailSchema parse the API's own shapes", () => {
    expect(jobSummarySchema.parse(summary)).toEqual(summary);
    expect(
      jobDetailSchema.parse({ ...summary, failures: [], volumes: [volume] }),
    ).toMatchObject({ volumes: [volume] });
  });

  test("an unknown phase is rejected, not coerced to a neutral value", () => {
    expect(() =>
      jobSummarySchema.parse({ ...summary, phase: "Bogus" }),
    ).toThrow();
  });

  test("reason is optional on a volume row", () => {
    const withReason = { ...volume, reason: "exit 1" };
    expect(
      jobDetailSchema.parse({
        ...summary,
        failures: [],
        volumes: [withReason],
      }),
    ).toMatchObject({ volumes: [{ reason: "exit 1" }] });
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
