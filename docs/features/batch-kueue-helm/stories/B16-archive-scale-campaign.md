---
type: Product Backlog Item
id: 2895
parent: 2800
title: Run an archive-scale campaign and measure it
---

# B16 · Run an archive-scale campaign and measure it

**Story.** As the product owner, I want one real campaign — enough volumes
to run unattended for days — completed through the system, with throughput
and GPU-utilisation numbers I can put in front of management, so that we
know what a GPU-hour buys us and whether any further engineering is
justified.

## Why it matters

Everything so far has been validated on single volumes and one 480-page
run. The design's central bet is that streaming pages keeps the GPU busy;
the number that tests it is *how much of the wall-clock the GPU spent
waiting for images*. That figure is recorded in every completed volume's
provenance file, but we have never aggregated it over a real campaign. It
is also the trigger that decides whether the image cache (B19) is worth
building.

## What this delivers

- A campaign of agreed size (suggestion: 100+ volumes, mixed sizes, one
  production pipeline) declared in the campaigns repo and run to completion.
- A short script that reads every completed volume's provenance file and
  reports pages/hour, GPU-stall fraction, failure rate and retry counts.
- A one-page write-up of the numbers and what they imply.

## Done when

- [ ] B10 (results on the HCP), B11 (campaigns repo governance) and B15
      (production cluster) are done and the image allow-list is set.
- [ ] The campaign completes with failed volumes explained.
- [ ] Aggregate figures published in the docs; a go/no-go on B19 recorded.
