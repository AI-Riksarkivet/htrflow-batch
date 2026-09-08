import { describe, expect, test } from "vitest";
import { ApiUnreachable, type VolumeReason } from "./api.js";
import {
  describeApiError,
  describeReason,
  describeNotice,
  describeProgress,
  describeUnreadable,
} from "./reasons.js";

/**
 * The wording of every sentence a reader can meet is pinned here, verbatim.
 * These strings are the product — "the error messages need to be a lot more
 * human friendly" (B63 Task 20G) — so a change to one is a deliberate edit of
 * this table, not a surprise from a refactor.
 */
const reason = (r: Partial<VolumeReason>): VolumeReason => ({
  stage: null,
  permanent: null,
  error: "",
  ...r,
});

describe("describeReason", () => {
  // MAX_SECONDS is the same failure written by a wrapper older than Task 25.
  test.each(["DeadlineExceeded", "MAX_SECONDS"])(
    "the pod's time budget, reported as %s",
    (error) => {
      expect(reasonOf({ stage: "stream", permanent: false, error })).toBe(
        "Stopped when this volume's time budget ran out; the next attempt " +
          "resumes from the pages already finished.",
      );
    },
  );

  test("a drain or a pause", () => {
    expect(
      reasonOf({ stage: "stream", permanent: false, error: "SIGTERM" }),
    ).toBe(
      "The pod was stopped by the cluster (a node drain or a pause); the " +
        "volume will be retried.",
    );
  });

  test("a manifest that could not be read", () => {
    expect(
      reasonOf({
        stage: "setup",
        permanent: true,
        error: "manifest is not JSON: https://iiif.example.org/x/manifest",
      }),
    ).toBe(
      "The IIIF manifest could not be read: manifest is not JSON: " +
        "https://iiif.example.org/x/manifest. Fix the manifest URL in the " +
        "campaign file — this volume will not be retried.",
    );
  });

  test("pages that failed the verify gate, with the page names", () => {
    expect(
      reasonOf({
        stage: "verify",
        permanent: false,
        error:
          "verify failed: 2 missing, 1 failed errors: p101: boom " +
          "missing=['p012', 'p045'] failed=['p101']",
      }),
    ).toBe(
      "3 pages could not be processed (p012, p045, p101); the volume is " +
        "retried automatically and only those pages are redone.",
    );
  });

  test("more failed pages than the sentence spells out", () => {
    expect(
      reasonOf({
        stage: "verify",
        permanent: false,
        error: "verify failed: missing=['a', 'b', 'c', 'd', 'e'] failed=[]",
      }),
    ).toBe(
      "5 pages could not be processed (a, b, c and 2 more); the volume is " +
        "retried automatically and only those pages are redone.",
    );
  });

  test("one failed page is singular", () => {
    expect(
      reasonOf({ stage: "verify", error: "verify failed: failed=['p7']" }),
    ).toContain("1 page could not be processed (p7);");
  });

  test("a verify message whose page lists were truncated away", () => {
    expect(
      reasonOf({
        stage: "verify",
        error: "verify failed: 900 missing, 0 f...",
      }),
    ).toBe(
      "Some pages could not be processed; the volume is retried " +
        "automatically and only those pages are redone.",
    );
  });

  test.each([
    ["setup", "reading the manifest"],
    ["resume", "checking earlier results"],
    ["load", "loading the model"],
    ["stream", "processing pages"],
    ["verify", "checking results"],
    ["publish", "publishing results"],
  ])("stage %s reads as '%s'", (stage, words) => {
    expect(reasonOf({ stage, permanent: false, error: "boom" })).toBe(
      `Failed while ${words}: boom. It will be retried automatically.`,
    );
  });

  test("a bad env is a deployment problem, not a manifest one", () => {
    // The counter-case to the row above: same permanence, different stage,
    // and a reader sent to converter.yaml instead of the campaign file.
    expect(
      reasonOf({
        stage: "config",
        permanent: true,
        error: "missing required env: S3_BUCKET",
      }),
    ).toBe(
      "The volume's settings are incomplete or wrong: missing required env: " +
        "S3_BUCKET. This is a deployment problem, not a manifest problem — " +
        "check the campaign's converter.yaml and the chart values.",
    );
  });

  test("an unknown permanent failure still says what to do next", () => {
    expect(
      reasonOf({ stage: "load", permanent: true, error: "unknown step 'Foo'" }),
    ).toBe(
      "Failed while loading the model: unknown step 'Foo'. This volume will " +
        "not be retried — fix the cause, then put the volume in a new " +
        "campaign.",
    );
  });

  test("a permanently bad pipeline config fails the warm-up for good", () => {
    expect(
      reasonOf({
        stage: "warmup",
        permanent: true,
        error: "unknown model class 'Yolo9'",
      }),
    ).toBe(
      "The warm-up failed: unknown model class 'Yolo9'. Fix the pipeline " +
        "file, then re-apply it — the warm-up will not retry on its own.",
    );
  });

  test("a transiently failed warm-up still needs a re-apply, not just time", () => {
    // The warm-up Job's own backoffLimit has already exhausted its retries
    // by the time the API reports "failed" (Task 28).
    expect(
      reasonOf({
        stage: "warmup",
        permanent: false,
        error: "connection reset",
      }),
    ).toBe(
      "The warm-up failed: connection reset. Re-apply the pipeline to try " +
        "again.",
    );
  });

  test("an unknown stage falls back to a plain sentence", () => {
    expect(
      reasonOf({ stage: "teleport", permanent: false, error: "boom" }),
    ).toBe("Failed: boom. It will be retried automatically.");
  });

  test("a raw message that is JSON never reaches the reader as JSON", () => {
    expect(reasonOf({ error: '{"stage": "setup", "permanent": true}' })).toBe(
      "The pod stopped without a message this page can read; open the run " +
        "log to see what happened.",
    );
  });

  test("a raw, unparsed termination message is still a sentence", () => {
    expect(reasonOf({ error: "Killed" })).toBe("Failed: Killed.");
  });

  test("a message that already ends in a full stop does not gain a second", () => {
    expect(reasonOf({ error: "Out of memory." })).toBe(
      "Failed: Out of memory.",
    );
  });
});

function reasonOf(r: Partial<VolumeReason>): string {
  return describeReason(reason(r));
}

describe("describeApiError", () => {
  test("a non-2xx, with the last list still on screen", () => {
    expect(describeApiError(new ApiUnreachable("HTTP 503"), true)).toBe(
      "Can't reach the campaign service right now (HTTP 503). Showing the " +
        "list we last received. Retrying every 60 seconds.",
    );
  });

  test("a non-2xx with nothing on screen yet", () => {
    expect(describeApiError(new ApiUnreachable("HTTP 500"), false)).toBe(
      "Can't reach the campaign service right now (HTTP 500). Retrying " +
        "every 60 seconds.",
    );
  });

  test("a network error keeps its transport detail out of the sentence", () => {
    const sentence = describeApiError(
      new ApiUnreachable("Failed to fetch"),
      false,
    );
    expect(sentence).toBe(
      "Can't reach the campaign service right now. Retrying every 60 seconds.",
    );
  });

  test("a 404 is a campaign that is gone, not an outage", () => {
    expect(describeApiError(new ApiUnreachable("HTTP 404"), true)).toBe(
      "This campaign no longer exists (finished campaigns are removed after " +
        "24 hours).",
    );
  });

  test("anything else is a version mismatch, and never a ZodError dump", () => {
    const sentence = describeApiError(
      new Error("invalid_type at volumes.0"),
      true,
    );
    expect(sentence).toBe(
      "The campaign service answered in a form this page doesn't " +
        "understand. Reload the page; if it keeps happening, the page and " +
        "the service are running different versions.",
    );
    expect(sentence).not.toContain("invalid_type");
  });
});

describe("describeUnreadable", () => {
  test("one hidden campaign, with the next step", () => {
    expect(describeUnreadable(1)).toBe(
      "1 campaign could not be read and is not shown. Reload the page; if it " +
        "keeps happening, the page and the service are running different versions.",
    );
  });
  test("plural", () => {
    expect(describeUnreadable(3)).toMatch(
      /^3 campaigns could not be read and are not shown\./,
    );
  });
});

describe("describeProgress", () => {
  const progress = {
    done: 137,
    total: 638,
    failed: 0,
    lastPage: "0137",
    stage: "stream",
    updatedAt: "2026-09-08T09:31:00+00:00",
    ageSeconds: 12,
    lastError: null,
    errors: 0,
    viewerPublished: true,
  };

  test("pages, what it is doing, and how long ago", () => {
    expect(describeProgress(progress)).toBe(
      "137 / 638 pages · processing pages · updated 12 s ago",
    );
  });

  test("minutes and hours once seconds stop meaning anything", () => {
    expect(describeProgress({ ...progress, ageSeconds: 300 })).toContain(
      "updated 5 min ago",
    );
    expect(describeProgress({ ...progress, ageSeconds: 7200 })).toContain(
      "updated 2 h ago",
    );
  });

  test("failed pages are named, since the count alone hides them", () => {
    expect(describeProgress({ ...progress, failed: 2 })).toContain("2 failed");
  });

  test("a stage with no word of its own is still shown, not dropped", () => {
    expect(describeProgress({ ...progress, stage: "done" })).toContain(
      "· done ·",
    );
  });

  test("no timestamp, no stage: just the pages", () => {
    expect(
      describeProgress({
        ...progress,
        stage: null,
        updatedAt: null,
        ageSeconds: null,
        lastPage: null,
      }),
    ).toBe("137 / 638 pages");
  });

  test("the age comes from the API's own clock, never Date.now()", () => {
    // ageSeconds is what renders; a null updatedAt with a non-null
    // ageSeconds would be a shape the API never sends, but the point is
    // that this function never reads a clock of its own to compute it.
    expect(
      describeProgress({ ...progress, updatedAt: null, ageSeconds: 12 }),
    ).toContain("updated 12 s ago");
  });
});

describe("describeNotice", () => {
  const lastError = {
    page: "0044",
    error: "htrflow's Segmentation worker thread died",
    volume: "vol1",
    logUrl: "https://pub/status/logs/demo-v1/vol1.txt",
  };

  test("nothing wrong, no notice at all", () => {
    expect(
      describeNotice({ pagesFailed: 0, errors: 0, lastError: null }),
    ).toBeNull();
  });

  test("the count and the sentence behind it", () => {
    expect(describeNotice({ pagesFailed: 1, errors: 2, lastError })).toBe(
      "1 page failed · 2 errors · page 0044: htrflow's Segmentation " +
        "worker thread died",
    );
  });

  test("plurals", () => {
    expect(describeNotice({ pagesFailed: 3, errors: 1, lastError: null })).toBe(
      "3 pages failed · 1 error",
    );
  });

  test("errors alone are still worth saying", () => {
    expect(describeNotice({ pagesFailed: 0, errors: 4, lastError: null })).toBe(
      "4 errors",
    );
  });

  test("an error with no page name still reads as a sentence", () => {
    expect(
      describeNotice({
        pagesFailed: 1,
        errors: 0,
        lastError: { ...lastError, page: null },
      }),
    ).toBe("1 page failed · htrflow's Segmentation worker thread died");
  });
});
