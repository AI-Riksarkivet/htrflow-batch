---
type: Product Backlog Item
id: 2843
parent: 2800
title: Queue volumes fairly on the GPUs and fail safely
---

# B02 · Queue volumes fairly on the GPUs and fail safely

**Story.** As the person responsible for the GPU cluster, I want submitted
volumes to wait in a queue and run only when a GPU is free, so that a big
campaign cannot starve other users or crash the node, and a failed volume is
retried a bounded number of times and then reported — never silently
forgotten.

## Why it matters

Kubernetes will happily start every job you give it at once; with GPUs that
means out-of-memory crashes and nothing finishing. And a job that fails
needs a policy: retry the transient (network hiccup), don't retry the
permanent (bad manifest), and stop retrying at some point.

## What this delivers

- **One volume = one Kubernetes Job**, created *suspended* and released by
  **Kueue** when GPU quota is free. Queue order is first-in-first-out; the
  quota (how many GPUs the batch system may use) is one number in the chart.
- **Deterministic names.** A given volume + pipeline always gets the same
  job name, so submitting it twice is a harmless no-op, not a duplicate run.
- **A deadline that follows the volume size**, so a hung job on a 20-page
  volume is killed in minutes, not hours, while a 900-page volume gets the
  time it needs.
- **Failure policy by exit code.** Permanent failures (bad input) are not
  retried; transient ones are, up to a budget; a job that was making progress
  when the node evicted it is *not* charged against that budget.
- **Graceful shutdown.** On eviction the job finishes the page in flight,
  ships its log, and exits with a code the retry logic understands.

## Done when

- [ ] With quota set to N, at most N volume jobs run at once; the rest show
      as queued.
- [ ] Submitting the same volume twice creates one job.
- [ ] A job that exceeds its size-derived deadline is killed and reported.
- [ ] A permanently failing volume is retried zero times; a transiently
      failing one up to the configured budget; the verdict is visible in the
      status page.
- [ ] Evicting a running pod does not count as a failed attempt.
