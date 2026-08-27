---
type: Product Backlog Item
id: 2897
parent: 2800
title: Let urgent volumes jump the queue
---

# B18 · Let urgent volumes jump the queue

**Story.** As an archivist with a researcher waiting, I want to mark a
volume or campaign as urgent so that it runs ahead of the bulk backlog,
without anyone having to pause the backlog by hand.

## Why it matters

Once a large campaign is queued, a single requested volume would otherwise
wait behind hundreds of others. Kueue supports priority classes natively;
the work is exposing that as one word in the campaign file.

## What this delivers

- Two priority classes (`htr-interactive` above `htr-bulk`) defined by the
  chart.
- A `priority:` field on a campaign (default bulk) that the reconciler
  maps onto the job.

## Done when

- [ ] With the bulk queue full, an interactive-priority volume is admitted
      next.
- [ ] Documented in the campaign YAML reference.
