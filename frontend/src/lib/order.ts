// The order ten campaigns are worth reading in.
//
// The API sorts by creation date, which is the order they were declared in
// and says nothing about which one needs a person (the product owner,
// 2026-09-16). Four bands, worst-first in the sense of "look here now":
// what is moving, what went wrong, what is over, and what has not begun.
// Stable inside each band, so a poll never shuffles the list under a reader.
import type { JobSummary } from "./api.js";

/**
 * Nothing is running while the pipeline's models are not on the node —
 * whether the warm-up Job failed or was never created at all. The one
 * exception is a campaign that already succeeded: an old pipeline that never
 * had a warm-up Job must not be marked as blocked after the fact.
 */
export function warmupBlocked(job: JobSummary): boolean {
  return (
    job.warmup.phase === "failed" ||
    (job.warmup.phase === "missing" && job.phase !== "Succeeded")
  );
}

/**
 * Something about this campaign wants a person. Exported because the card's
 * left accent asks exactly the same question, and the two were separate
 * copies of the rule until one of them forgot about a missing warm-up
 * (2026-09-16 review).
 */
export function inTrouble(job: JobSummary): boolean {
  return (
    job.phase === "Failed" ||
    job.phase === "PartiallyFailed" ||
    job.counts.failed > 0 ||
    warmupBlocked(job)
  );
}

/** 0 running · 1 something wrong · 2 over · 3 not started. */
function band(job: JobSummary): number {
  if (job.phase === "Running" && !warmupBlocked(job)) return 0;
  if (inTrouble(job)) return 1;
  if (job.phase === "Queued" || job.phase === "Paused") return 3;
  return 2;
}

/** Newest finish first; a campaign with no finish time goes last of its band. */
function finishedLast(job: JobSummary): string {
  return job.finishedAt ?? "";
}

export function byAttention(jobs: JobSummary[]): JobSummary[] {
  return [...jobs].sort((a, b) => {
    const bands = band(a) - band(b);
    if (bands !== 0) return bands;
    if (band(a) !== 2) return 0; // stable: the order the API sent
    return finishedLast(b).localeCompare(finishedLast(a));
  });
}
