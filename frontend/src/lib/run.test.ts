import { describe, expect, test } from "vitest";
import {
  formatDuration,
  isTerminalManifest,
  pageStats,
  percentile,
  runManifestSchema,
  summarizeRun,
  type RunManifest,
} from "./run.js";

const base: RunManifest = {
  volume: "R1",
  pipeline_id: "p",
  htrflow_version: "0.2.6",
  image_digest: "reg/img@sha256:abcdef0123456789",
  pages: 3,
  results: {
    "0001": { status: "ok", seconds: 2.5 },
    "0002": { status: "ok", seconds: 1 },
    "0003": { status: "failed", seconds: 0.4, error: "boom" },
  },
};

describe("runManifestSchema", () => {
  test("accepts the wrapper's manifest with extra fields", () => {
    const parsed = runManifestSchema.safeParse({
      ...base,
      pipeline_sha256: "x",
      wall_seconds: 12.3,
      viewer_url: "http://x",
    });
    expect(parsed.success).toBe(true);
    if (parsed.success) expect(parsed.data.wall_seconds).toBe(12.3);
  });

  test("rejects a manifest without results", () => {
    const { results: _results, ...noResults } = base;
    expect(runManifestSchema.safeParse(noResults).success).toBe(false);
  });
});

describe("percentile", () => {
  test("interpolates linearly over a sorted list", () => {
    expect(percentile([1, 2, 3, 4], 0.5)).toBe(2.5);
    expect(percentile([1, 2, 3, 4, 5], 0.5)).toBe(3);
    expect(percentile([10], 0.95)).toBe(10);
    expect(percentile([], 0.5)).toBeNull();
  });
});

describe("summarizeRun", () => {
  test("counts statuses and totals seconds", () => {
    const s = summarizeRun(base.results);
    expect(s.pages).toBe(3);
    expect(s.ok).toBe(2);
    expect(s.failed).toBe(1);
    expect(s.skipped).toBe(0);
    expect(s.totalSeconds).toBeCloseTo(3.9);
  });

  test("timing stats exclude skipped pages", () => {
    const s = summarizeRun({
      "0001": { status: "ok", seconds: 4 },
      "0002": { status: "ok", seconds: 2 },
      "0003": { status: "skipped", seconds: 0 },
    });
    expect(s.skipped).toBe(1);
    expect(s.median).toBe(3);
    expect(s.max).toBe(4);
    expect(s.p95).toBeCloseTo(3.9);
  });

  test("slowest lists at most five pages, slowest first", () => {
    const results = Object.fromEntries(
      Array.from({ length: 8 }, (_, i) => [
        String(i + 1).padStart(4, "0"),
        { status: "ok", seconds: i + 1 },
      ]),
    );
    const s = summarizeRun(results);
    expect(s.slowest.map((p) => p.id)).toEqual([
      "0008",
      "0007",
      "0006",
      "0005",
      "0004",
    ]);
    expect(s.slowest[0]?.seconds).toBe(8);
  });

  test("failed pages carry their error, sorted by id", () => {
    const s = summarizeRun({
      "0009": { status: "failed", seconds: 0.1, error: "late" },
      "0001": { status: "failed", seconds: 0.2, error: "early" },
      "0005": { status: "ok", seconds: 1 },
    });
    expect(s.failedPages).toEqual([
      { id: "0001", status: "failed", seconds: 0.2, error: "early" },
      { id: "0009", status: "failed", seconds: 0.1, error: "late" },
    ]);
  });

  test("empty results give null timing stats", () => {
    const s = summarizeRun({});
    expect(s.pages).toBe(0);
    expect(s.median).toBeNull();
    expect(s.p95).toBeNull();
    expect(s.max).toBeNull();
    expect(s.slowest).toEqual([]);
  });
});

describe("page_sources", () => {
  test("parses, attaches http(s) sources to page stats, and tolerates absence", () => {
    const parsed = runManifestSchema.parse({
      ...base,
      page_sources: { "0001": "https://iiif/0001.jpg", "0002": "javascript:x" },
      canvas_ids: { "0001": "https://iiif/canvas/1", "0002": null },
    });
    const stats = pageStats(parsed.results, parsed.page_sources);
    expect(stats[0]?.source).toBe("https://iiif/0001.jpg");
    expect(stats[1]?.source).toBeUndefined(); // refused scheme
    expect(stats[2]?.source).toBeUndefined(); // no entry
    expect(pageStats(base.results)[0]?.source).toBeUndefined();
    expect(
      summarizeRun(parsed.results, parsed.page_sources).failedPages[0]?.source,
    ).toBeUndefined();
  });
});

describe("formatDuration", () => {
  test("seconds below a minute, h/min/s above", () => {
    expect(formatDuration(12.34)).toBe("12.3 s");
    expect(formatDuration(75)).toBe("1 min 15 s");
    expect(formatDuration(6217.7)).toBe("1 h 43 min 38 s");
  });
});

describe("isTerminalManifest", () => {
  test("terminal once every page has a result", () => {
    expect(isTerminalManifest(base)).toBe(true);
  });
  test("not terminal while results are missing", () => {
    expect(isTerminalManifest({ ...base, pages: 5 })).toBe(false);
  });
  test("an empty run is not terminal", () => {
    expect(isTerminalManifest({ ...base, pages: 0, results: {} })).toBe(false);
  });
});
