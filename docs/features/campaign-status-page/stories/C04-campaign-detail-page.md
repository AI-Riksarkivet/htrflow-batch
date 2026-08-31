---
type: Product Backlog Item
id:
parent: 2923
title: Campaign detail page
---

# C04 · Campaign detail page

**Story.** As a data scientist running a campaign, I want a page per campaign showing progress over time, an estimated finish based on measured throughput, and the list of failed or waiting volumes with their reasons, so that I can answer "when will it be done and what is stuck" without opening a terminal.

## Why it matters

Today a campaign is one card with a volume table. Counts are there; the trend, the estimate and the "what needs a human" list are not — they have to be read out of the table by eye.

## What this delivers

- A `/campaign/<id>` route: header with the campaign's pipeline and repo link; a progress-over-time chart from the tick history (kept by the reconciler in the status area, small, rolling); an ETA from pages/hour over the last N ticks; sections for *needs attention*, *failed and retrying*, *waiting on warm-up*, each with the reason and the link to the run log.
- Still no backend: the history is one more small file the reconciler writes; the page reads it.

## Done when

- [ ] For a running campaign on DEV the page shows a chart, an ETA and the stuck volumes with reasons; the ETA is within a reasonable margin of the actual finish on a test campaign.
