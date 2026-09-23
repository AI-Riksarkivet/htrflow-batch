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
      "2 pages are missing from the results (p012, p045); the volume is " +
        "retried automatically and only those pages are redone.",
    );
  });

  test("every page in the attempt failed: a broken model, not a volume", () => {
    expect(
      reasonOf({
        stage: "verify",
        error:
          "verify failed: all 3 processed pages failed errors: p101: boom " +
          "failed=['p101', 'p102', 'p103']",
      }),
    ).toBe(
      "None of the 3 pages processed in this attempt produced a result; the " +
        "volume is retried automatically — check the model and the GPU.",
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
      "5 pages are missing from the results (a, b, c and 2 more); the volume " +
        "is retried automatically and only those pages are redone.",
    );
  });

  test("one failed page is singular", () => {
    expect(
      reasonOf({ stage: "verify", error: "verify failed: failed=['p7']" }),
    ).toContain("1 page is missing from the results (p7);");
  });

  test("a verify message whose page lists were truncated away", () => {
    // The count is still there to read; only a message that has neither a
    // count nor one whole page name says "some".
    expect(
      reasonOf({
        stage: "verify",
        error: "verify failed: 900 missing, 0 f...",
      }),
    ).toBe(
      "900 pages are missing from the results; the volume is retried " +
        "automatically and only those pages are redone.",
    );
    expect(
      reasonOf({ stage: "verify", error: "verify failed: missing=['p0..." }),
    ).toBe(
      "Some pages are missing from the results; the volume is retried " +
        "automatically and only those pages are redone.",
    );
  });

  // What the wrapper writes, in its own format: `_verify` builds both
  // messages (packages/wrapper/src/htrflow_batch/main.py:550 and :561) and
  // `terminate` clips the field at 3500 characters (main.py:115), which a
  // few hundred page names fill on their own. The counts come first so the
  // clip only ever takes names; the sentence must read them, not count the
  // names that survived.
  const pageList = (n: number) =>
    Array.from({ length: n }, (_, i) => `'p${String(i + 1).padStart(4, "0")}'`);
  const clipped = (message: string) =>
    message.length > 3500 ? `${message.slice(0, 3500)}...(truncated)` : message;
  const detail = (n: number) =>
    " errors: " +
    pageList(Math.min(n, 10))
      .map((p) => `${p.slice(1, -1)}: CUDA error: out of memory`)
      .join("; ") +
    (n > 10 ? ` (+${n - 10} more)` : "");

  test("main.py:550, clipped: the missing count is the wrapper's, not the names left", () => {
    const error = clipped(
      `verify failed: 600 missing, 0 failed missing=[${pageList(600).join(", ")}] failed=[]`,
    );
    expect(error).toMatch(/\.\.\.\(truncated\)$/);
    expect(reasonOf({ stage: "verify", permanent: false, error })).toBe(
      "600 pages are missing from the results (p0001, p0002, p0003 and 597 " +
        "more); the volume is retried automatically and only those pages " +
        "are redone.",
    );
  });

  test("main.py:561, clipped: every page failed, however many names were cut", () => {
    const error = clipped(
      `verify failed: all 400 processed pages failed${detail(400)} ` +
        `failed=[${pageList(400).join(", ")}]`,
    );
    expect(error).toMatch(/\.\.\.\(truncated\)$/);
    expect(reasonOf({ stage: "verify", permanent: false, error })).toBe(
      "None of the 400 pages processed in this attempt produced a result; " +
        "the volume is retried automatically — check the model and the GPU.",
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

/** A volume whose index still has retries left: the pod is being retried. */
function reasonOf(r: Partial<VolumeReason>): string {
  return describeReason(reason(r), false);
}

/**
 * A volume in the Job's failedIndexes: backoffLimitPerIndex is spent, so
 * nothing retries it however transient the cause was. Saying "it will be
 * retried" there left a reader waiting for a retry that never comes (the
 * 2026-09-17 audit, 3078).
 */
describe("describeReason for a volume with no retries left", () => {
  const final = (r: Partial<VolumeReason>) => describeReason(reason(r), true);

  test("a drain or a pause", () => {
    expect(final({ stage: "stream", permanent: false, error: "SIGTERM" })).toBe(
      "The pod was stopped by the cluster (a node drain or a pause), and " +
        "the volume has used all its retries — put it in a new campaign to " +
        "run it again.",
    );
  });

  test("the time budget", () => {
    expect(
      final({ stage: "stream", permanent: false, error: "DeadlineExceeded" }),
    ).toBe(
      "Stopped when this volume's time budget ran out, and it has used all " +
        "its retries — put the volume in a new campaign to run it again.",
    );
  });

  test("pages missing after verify", () => {
    expect(
      final({
        stage: "verify",
        permanent: false,
        error: "verify failed: missing=['p012', 'p045'] failed=[]",
      }),
    ).toBe(
      "2 pages are missing from the results (p012, p045), and the volume " +
        "has used all its retries — put it in a new campaign to redo them.",
    );
  });

  test("every page in the last attempt failed", () => {
    expect(
      final({
        stage: "verify",
        error:
          "verify failed: all 3 processed pages failed failed=['a', 'b', 'c']",
      }),
    ).toBe(
      "None of the 3 pages processed in the last attempt produced a result, " +
        "and the volume has used all its retries — check the model and the " +
        "GPU, then put it in a new campaign.",
    );
  });

  test("any other transient failure", () => {
    expect(final({ stage: "stream", permanent: false, error: "boom" })).toBe(
      "Failed while processing pages: boom. The volume has used all its " +
        "retries — put it in a new campaign to run it again.",
    );
  });

  test("a permanent failure reads the same either way", () => {
    const r = { stage: "load", permanent: true, error: "unknown step 'Foo'" };
    expect(final(r)).toBe(reasonOf(r));
  });

  test("a failure with no word on permanence promises nothing either way", () => {
    expect(final({ error: "Killed" })).toBe("Failed: Killed.");
  });
});

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
      "This campaign is gone: its campaign file has been removed from the " +
        "campaigns repo.",
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

  // The page counts moved to the status column's own figures, in the shape
  // zone 2 uses for the campaign; this says what the numbers cannot (the
  // product owner, 2026-09-16).
  test("what it is doing, and how long ago", () => {
    expect(describeProgress(progress, "active")).toBe(
      "processing pages · updated 12 s ago",
    );
  });

  test("the counts are not repeated here", () => {
    expect(
      describeProgress({ ...progress, failed: 2 }, "active"),
    ).not.toContain("2 failed");
    expect(describeProgress(progress, "active")).not.toContain("638");
  });

  test("minutes and hours once seconds stop meaning anything", () => {
    expect(
      describeProgress({ ...progress, ageSeconds: 300 }, "active"),
    ).toContain("updated 5 min ago");
    expect(
      describeProgress({ ...progress, ageSeconds: 7200 }, "active"),
    ).toContain("updated 2 h ago");
  });

  test("a stage with no word of its own is still shown, not dropped", () => {
    expect(describeProgress({ ...progress, stage: "warmup" }, "active")).toBe(
      "warmup · updated 12 s ago",
    );
  });

  // "updated 47 h ago" after every row of a finished campaign is a clock
  // nobody is waiting on (the product owner, 2026-09-16).
  test("the clock runs only while something can still change", () => {
    for (const state of ["done", "failed", "pending"] as const)
      expect(describeProgress(progress, state)).not.toContain("updated");
    for (const state of ["active", "unknown"] as const)
      expect(describeProgress(progress, state)).toContain("updated 12 s ago");
  });

  test("a finished volume adds nothing to its numbers", () => {
    // `done` is the state word's job, the pages are the figures', and a
    // clock nobody is waiting on is noise (the product owner, 2026-09-16).
    expect(
      describeProgress(
        {
          ...progress,
          done: 637,
          total: 638,
          failed: 1,
          stage: "done",
          ageSeconds: 12,
        },
        "done",
      ),
    ).toBe("");
  });

  test("no timestamp, no stage: nothing at all", () => {
    expect(
      describeProgress(
        {
          ...progress,
          stage: null,
          updatedAt: null,
          ageSeconds: null,
          lastPage: null,
        },
        "active",
      ),
    ).toBe("");
  });

  test("the age comes from the API's own clock, never Date.now()", () => {
    // ageSeconds is what renders; a null updatedAt with a non-null
    // ageSeconds would be a shape the API never sends, but the point is
    // that this function never reads a clock of its own to compute it.
    expect(
      describeProgress(
        { ...progress, updatedAt: null, ageSeconds: 12 },
        "active",
      ),
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

  // The wrapper writes the page into its own message as often as not, and
  // the API sends the page beside it: prefixing again read "page 0044: page
  // 0044: htrflow's ..." on a live campaign (the product owner, 2026-09-16).
  test("a message that already names its page is not given the page twice", () => {
    expect(
      describeNotice({
        pagesFailed: 1,
        errors: 0,
        lastError: {
          ...lastError,
          error: "page 0044: htrflow's Segmentation worker thread died",
        },
      }),
    ).toBe(
      "1 page failed · page 0044: htrflow's Segmentation worker thread died",
    );
  });

  test("a message naming a different page keeps both", () => {
    expect(
      describeNotice({
        pagesFailed: 1,
        errors: 0,
        lastError: { ...lastError, error: "page 0002: HTTP 400" },
      }),
    ).toBe("1 page failed · page 0044: page 0002: HTTP 400");
  });

  test("a message that merely starts with the word page is untouched", () => {
    expect(
      describeNotice({
        pagesFailed: 1,
        errors: 0,
        lastError: { ...lastError, error: "pages were skipped" },
      }),
    ).toBe("1 page failed · page 0044: pages were skipped");
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
