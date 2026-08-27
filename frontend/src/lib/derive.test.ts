import { describe, expect, test } from "vitest";
import {
  campaignHealth,
  isHttpUrl,
  isStale,
  pagesLabel,
  shortDate,
  tickSummaryLabel,
  viewerHref,
} from "./derive.js";
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
  run_manifest: null,
  updated: null,
  failure_log: null,
  run_log: null,
  terminal: null,
};
const pending: VolumeEntry = {
  ...done,
  status: "pending",
  viewer_manifest: null,
};

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

describe("derive", () => {
  test("a volume without a usable manifest has no viewer link", () => {
    expect(viewerHref({ ...pending, source_manifest: null })).toBeNull();
  });

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

  test("campaign health: worst volume wins", () => {
    const st = (...s: VolumeEntry["status"][]) =>
      s.map((status) => ({ status }));
    expect(campaignHealth(st("done", "done"))).toBe("done");
    expect(campaignHealth(st("done", "running"))).toBe("active");
    expect(campaignHealth(st("done", "queued", "retry"))).toBe("active");
    expect(campaignHealth(st("done", "deleting"))).toBe("active"); // retry in progress
    expect(campaignHealth(st("running", "needs-attention"))).toBe("failed");
    expect(campaignHealth(st("done", "unreachable"))).toBe("failed");
    expect(campaignHealth(st("pending", "pending"))).toBe("idle");
    expect(campaignHealth(st("done", "pending"))).toBe("idle");
    expect(campaignHealth([])).toBe("idle");
  });

  test("tick summary label lists only the fields present", () => {
    expect(
      tickSummaryLabel({
        seconds: 4.06,
        s3_calls: 12,
        validations: 3,
        submitted: 1,
        retried: 0,
      }),
    ).toBe(
      "last tick 4.1 s · 12 S3 calls · 3 validations · 1 submitted · 0 retried",
    );
    expect(tickSummaryLabel({ seconds: 0.5 })).toBe("last tick 0.5 s");
    expect(tickSummaryLabel({})).toBeNull();
    expect(tickSummaryLabel(null)).toBeNull();
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

describe("shortDate", () => {
  test("formats an ISO timestamp", () => {
    expect(shortDate("2026-08-25T14:32:00Z", "UTC")).toBe("25 Aug, 14:32");
  });
  test("null and junk stay null", () => {
    expect(shortDate(null)).toBeNull();
    expect(shortDate("not-a-date")).toBeNull();
  });
});
