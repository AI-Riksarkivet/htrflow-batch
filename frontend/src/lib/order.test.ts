import { describe, expect, test } from "vitest";
import type { JobSummary } from "./api.js";
import { byAttention } from "./order.js";

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
