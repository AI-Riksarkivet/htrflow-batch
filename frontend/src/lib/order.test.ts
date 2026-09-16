import { describe, expect, test } from "vitest";
import type { JobSummary } from "./api.js";
import { byAttention, inTrouble, warmupBlocked } from "./order.js";

function job(name: string, over: Partial<JobSummary> = {}): JobSummary {
  return {
    namespace: "htr-test",
    name,
    pipeline: "demo-v1",
    phase: "Queued",
    counts: { total: 3, active: 0, done: 0, failed: 0 },
    suspended: false,
    createdAt: "2026-09-01T00:00:00Z",
    finishedAt: null,
    resultsBase: "https://results.example.org/htr-test/demo-v1",
    warmup: { phase: "succeeded" },
    jobGone: false,
    ...over,
  };
}

const running = job("running", {
  phase: "Running",
  counts: { total: 3, active: 1, done: 1, failed: 0 },
});
const partial = job("partial", { phase: "PartiallyFailed" });
const failed = job("failed", { phase: "Failed" });
const lostVolume = job("lost", {
  phase: "Succeeded",
  counts: { total: 3, active: 0, done: 2, failed: 1 },
});
const coldWarmup = job("cold", {
  phase: "Running",
  warmup: {
    phase: "failed",
    reason: { stage: null, permanent: null, error: "x" },
  },
});
const oldFinish = job("old", {
  phase: "Succeeded",
  finishedAt: "2026-09-01T10:00:00Z",
});
const newFinish = job("new", {
  phase: "Succeeded",
  finishedAt: "2026-09-08T10:00:00Z",
});
const queued = job("queued");
const paused = job("paused", { phase: "Paused" });

function order(jobs: JobSummary[]): string[] {
  return byAttention(jobs).map((j) => j.name);
}

describe("byAttention", () => {
  test("running first, then problems, then finished, then waiting", () => {
    expect(order([queued, newFinish, partial, running])).toEqual([
      "running",
      "partial",
      "new",
      "queued",
    ]);
  });

  test("every kind of problem lands in the same band", () => {
    expect(order([newFinish, failed, lostVolume, partial, running])).toEqual([
      "running",
      "failed",
      "lost",
      "partial",
      "new",
    ]);
  });

  test("a campaign whose warm-up failed is not running, whatever the phase says", () => {
    // Its pods cannot start: nothing is happening there, and the thing to
    // look at is why.
    expect(order([coldWarmup, running])).toEqual(["running", "cold"]);
  });

  test("finished campaigns read newest-first", () => {
    expect(order([oldFinish, newFinish])).toEqual(["new", "old"]);
    expect(order([newFinish, oldFinish])).toEqual(["new", "old"]);
  });

  test("a finished campaign with no finish time sorts after the ones that have one", () => {
    const undated = job("undated", { phase: "Succeeded" });
    expect(order([undated, oldFinish])).toEqual(["old", "undated"]);
  });

  test("a campaign nobody recorded the ending of is finished, not waiting", () => {
    const unknown = job("unknown", { phase: "Unknown", jobGone: true });
    expect(order([queued, unknown])).toEqual(["unknown", "queued"]);
  });

  test("the order inside a band is the one the API sent", () => {
    const a = job("a", { phase: "Queued" });
    const b = job("b", { phase: "Paused" });
    const c = job("c", { phase: "Queued" });
    expect(order([c, a, b])).toEqual(["c", "a", "b"]);
  });

  test("the list handed in is not reordered under its owner", () => {
    const given = [queued, running];
    byAttention(given);
    expect(given.map((j) => j.name)).toEqual(["queued", "running"]);
  });
});

// The card's left accent and the list's order have to agree about what
// "something is wrong" means: they were two copies of the rule, and one of
// them had forgotten that a pipeline with no warm-up Job at all blocks a
// campaign just as surely as one that failed (2026-09-16 review).
describe("warmupBlocked", () => {
  test("a failed warm-up blocks the campaign", () => {
    expect(
      warmupBlocked(
        job("x", {
          warmup: {
            phase: "failed",
            reason: { stage: null, permanent: null, error: "x" },
          },
        }),
      ),
    ).toBe(true);
  });

  test("no warm-up Job at all blocks it too", () => {
    expect(warmupBlocked(job("x", { warmup: { phase: "missing" } }))).toBe(
      true,
    );
  });

  test("...but not a campaign that already succeeded without one", () => {
    // An old pipeline that never had a warm-up Job must not paint a
    // finished campaign red.
    expect(
      warmupBlocked(
        job("x", { phase: "Succeeded", warmup: { phase: "missing" } }),
      ),
    ).toBe(false);
  });

  test("a warm-up still running is not a problem", () => {
    expect(warmupBlocked(job("x", { warmup: { phase: "pending" } }))).toBe(
      false,
    );
  });
});

describe("inTrouble", () => {
  test("is what the list and the card both ask", () => {
    expect(inTrouble(job("x", { phase: "Failed" }))).toBe(true);
    expect(inTrouble(job("x", { phase: "PartiallyFailed" }))).toBe(true);
    expect(
      inTrouble(
        job("x", { counts: { total: 3, active: 0, done: 2, failed: 1 } }),
      ),
    ).toBe(true);
    expect(inTrouble(job("x", { warmup: { phase: "missing" } }))).toBe(true);
    expect(inTrouble(job("x", { phase: "Running" }))).toBe(false);
  });
});

describe("byAttention uses the same rule", () => {
  test("a campaign whose pipeline has no warm-up sorts with the problems", () => {
    const noWarmup = job("cold", {
      phase: "Running",
      warmup: { phase: "missing" },
    });
    const running = job("running", { phase: "Running" });
    const finished = job("finished", {
      phase: "Succeeded",
      finishedAt: "2026-09-08T10:00:00Z",
    });
    expect(
      byAttention([finished, noWarmup, running]).map((j) => j.name),
    ).toEqual(["running", "cold", "finished"]);
  });
});
