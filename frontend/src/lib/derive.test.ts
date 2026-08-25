import { describe, expect, test } from "vitest";
import { isStale, pagesLabel, progress, viewerHref } from "./derive.js";
import type { VolumeEntry } from "./status.js";

const done: VolumeEntry = {
  id: "R1",
  status: "done",
  attempts: 0,
  pages_done: 638,
  pages_total: 638,
  error: null,
  viewer_manifest: "http://pub/htr-results/demo-v1/R1/iiif.json",
  source_manifest: "https://lbiiif.riksarkivet.se/arkis!R1/manifest",
  thumbnail: null,
};
const pending: VolumeEntry = { ...done, status: "pending", viewer_manifest: null };

describe("derive", () => {
  test("done volumes open the published manifest", () => {
    expect(viewerHref(done)).toBe(
      "uv.html#?manifest=http://pub/htr-results/demo-v1/R1/iiif.json",
    );
  });

  test("pending volumes open the source manifest", () => {
    expect(viewerHref(pending)).toBe(
      "uv.html#?manifest=https://lbiiif.riksarkivet.se/arkis!R1/manifest",
    );
  });

  test("progress percentage", () => {
    expect(progress({ done: 2, total: 8 })).toBe(25);
    expect(progress({ done: 0, total: 0 })).toBe(0);
  });

  test("stale when older than 3 ticks", () => {
    const now = new Date("2026-07-29T09:20:00Z");
    expect(isStale("2026-07-29T09:00:00Z", 300, now)).toBe(true);
    expect(isStale("2026-07-29T09:11:00Z", 300, now)).toBe(false);
  });
});

describe("pagesLabel", () => {
  test("renders d/t when total known", () => {
    expect(pagesLabel({ pages_done: 1, pages_total: 2 })).toBe("1/2 pages");
  });
  test("treats null done as 0", () => {
    expect(pagesLabel({ pages_done: null, pages_total: 2 })).toBe("0/2 pages");
  });
  test("hides when total unknown", () => {
    expect(pagesLabel({ pages_done: 3, pages_total: null })).toBeNull();
  });
});
