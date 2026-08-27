---
type: Product Backlog Item
id: 2858
parent: 2800
title: Audit fixes — the reconciler remembers, scales and retries correctly
---

# B29 · Audit fixes — the reconciler remembers, scales and retries correctly

**Story.** As the operator of a large campaign, I want a failed volume to
stay failed until a human clears it, a retry to be counted exactly once,
and the reconciler's five-minute pass to cost the same whether there are
15 volumes or 5 000 — so that the first archive-scale campaign does not
wedge the system or burn GPU hours re-running known failures.

## What was found (audit package A1)

- **X2 (high)** — the "needs attention" verdict lived only in the
  Kubernetes Job; after the Job's 24-hour clean-up the volume looked
  *pending* again and was resubmitted, forever.
- **X1 (critical)** — every pass probed every *done* volume with several
  S3 calls and did all its writes last; at thousands of volumes a pass
  would exceed its deadline, submit nothing, and repeat.
- **X3 (high)** — a retry deleted the Job before recording the attempt;
  any interruption in between lost the count. Manual runs could overlap a
  scheduled one.
- **X6 (high)** — a fresh install could loop model warm-ups; a healthy
  pass logged nothing.
- **X17 (medium)** — fairness across campaigns and the pipeline drift
  check had edge cases.
- Found live during remediation: the reconciler had **never actually
  deleted a failed Job** (arguments to the delete call were swapped), so
  retries burned attempts without re-running.

## What was done

- Terminal verdicts persisted in `attempts.json` and honoured regardless
  of whether the Job still exists; attempt counts written immediately
  after each bump; Jobs being deleted treated as "deleting", not retried.
- Tick cost made independent of done volumes (one list per pipeline,
  page counts read from the completion marker, bounded validations per
  pass with incremental persistence); status written and Jobs submitted
  *before* enrichment; a Lease per pass so manual runs cannot overlap;
  tick duration and cost reported in `status.json`.
- Warm-up capped and gated correctly; a green pass logs a summary.
- Every S3 or cluster effect wrapped per volume, so one bad response no
  longer aborts the whole pass.
- The delete bug fixed and the retry path verified end to end on the PoC.

## Done when

- [ ] A capped or permanently failed volume is never resubmitted without a
      human clearing it (tested; verified on the PoC after Job TTL).
- [ ] Tick cost is reported and does not grow with done volumes (tested
      with a fake bucket of thousands of volumes).
- [ ] Retry increments exactly once per real failure (tested).
