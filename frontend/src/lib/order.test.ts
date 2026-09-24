import { describe, expect, test } from "vitest";
import type { JobSummary } from "./api.js";
import {
  byAttention,
  inTrouble,
  keepOrder,
  outOfOrder,
  warmupBlocked,
} from "./order.js";

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
    qualityPrediction: false,
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

// A campaign that finished every volume but lost pages inside them belongs
// with the problems, not with the clean finishes (the product owner,
// 2026-09-16). The list endpoint sends no page counts, so what the sort can
// see is `counts.failed` — which is what a campaign with a failed VOLUME
// carries. A campaign that lost only pages is indistinguishable from a clean
// one until its card fetches its own detail; this pins what is knowable.
describe("a partially succeeded campaign in the list", () => {
  test("a failed volume puts it in the problems band, whatever its phase", () => {
    const lostVolume = job("lost", {
      phase: "Succeeded",
      counts: { total: 3, active: 0, done: 2, failed: 1 },
      finishedAt: "2026-09-08T10:00:00Z",
    });
    const clean = job("clean", {
      phase: "Succeeded",
      finishedAt: "2026-09-09T10:00:00Z",
    });
    // `clean` finished later, so only the band can put `lost` first.
    expect(byAttention([clean, lostVolume]).map((j) => j.name)).toEqual([
      "lost",
      "clean",
    ]);
    expect(inTrouble(lostVolume)).toBe(true);
  });
});

// A poll lands every minute on a page someone is reading. Re-sorting it
// moved a campaign that had just started from the bottom of the list to the
// top and every card between down one (the repo owner: "things pop all
// over"). The order a reader has is kept; what changed is said by the card
// itself, and new campaigns take the place the order gives them.
describe("keepOrder", () => {
  const names = (jobs: JobSummary[]) => jobs.map((j) => j.name);

  test("a campaign whose band changed stays where it was, with its new row", () => {
    const shown = byAttention([running, failed, oldFinish, queued]);
    const started = { ...queued, phase: "Running" as const };
    const kept = keepOrder(shown, [started, running, failed, oldFinish]);
    expect(names(kept)).toEqual(["running", "failed", "old", "queued"]);
    expect(kept.at(-1)?.phase).toBe("Running");
  });

  test("a new campaign goes where the order puts it among the rest", () => {
    const shown = byAttention([running, oldFinish, queued]);
    const kept = keepOrder(shown, [running, oldFinish, queued, failed, paused]);
    expect(names(kept)).toEqual([
      "running",
      "failed",
      "old",
      "queued",
      "paused",
    ]);
  });

  test("two new ones in a row keep the order's own order between them", () => {
    const shown = [queued];
    const kept = keepOrder(shown, [queued, newFinish, running]);
    expect(names(kept)).toEqual(["running", "new", "queued"]);
  });

  test("a campaign that is gone from the answer is gone from the list", () => {
    const shown = byAttention([running, failed, queued]);
    expect(names(keepOrder(shown, [running, queued]))).toEqual([
      "running",
      "queued",
    ]);
  });

  test("nothing shown yet: the order itself", () => {
    const all = [queued, oldFinish, running, failed];
    expect(names(keepOrder([], all))).toEqual(names(byAttention(all)));
  });

  test("the same name in two namespaces is two campaigns", () => {
    const other = { ...queued, namespace: "htr-other" };
    expect(
      keepOrder([queued], [other, queued]).map((j) => j.namespace),
    ).toEqual(["htr-other", "htr-test"]);
  });

  // What the order is for is surfacing trouble, and a kept order buried a
  // campaign that failed under the finished ones for as long as the tab
  // stayed open (review of this change). Falling into trouble is news worth
  // a move: the card goes to its place at once, as a new one would.
  test("a campaign that falls into trouble moves to its place at once", () => {
    const shown = byAttention([running, oldFinish, queued]);
    const broke = { ...queued, phase: "Failed" as const };
    expect(names(keepOrder(shown, [running, oldFinish, broke]))).toEqual([
      "running",
      "queued",
      "old",
    ]);
  });

  test("one already in trouble stays where the reader has it", () => {
    const shown = [oldFinish, failed, running]; // as a reader left it
    expect(names(keepOrder(shown, [running, failed, oldFinish]))).toEqual([
      "old",
      "failed",
      "running",
    ]);
  });
});

describe("outOfOrder", () => {
  test("says whether the sort would put the list another way", () => {
    expect(outOfOrder(byAttention([queued, running, failed]))).toBe(false);
    expect(outOfOrder([queued, running])).toBe(true);
  });
});
