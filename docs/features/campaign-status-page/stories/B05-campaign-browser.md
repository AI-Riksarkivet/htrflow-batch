---
type: Product Backlog Item
id: 2846
parent: 2923
title: See progress and live logs in the browser
---

# B05 · See progress and live logs in the browser

**Story.** As anyone with a stake in a campaign — archivist, project lead,
operator — I want a web page that shows every campaign and volume, what
state it is in, and lets me open a running volume's log or a finished
volume in the viewer, so that "how is it going?" never requires cluster
access.

## Why it matters

The batch system deliberately has no API and no database. The status page is
the *only* human-facing surface, so it has to be self-explanatory and honest:
if the reconciler has stopped, the page must say so rather than show stale
green ticks.

## What this delivers

- **A campaign browser**: one card per campaign, a table of its volumes
  (status, pages done / total, attempts, last update, links), served at the
  viewer's root address.
- **A run-log viewer** that shows a volume's log grouped by stage, with a
  summary (page timings, slowest pages, failed pages, a per-page grid) and a
  *live* mode that follows a running volume until it finishes.
- Clear banners when the status file is stale, when the reconciler reported
  errors, or when a volume is waiting on a model warm-up.
- Works on a phone-sized screen; keyboard-navigable; contrast-checked.
- No backend: the page reads two files from S3 and nothing else.

## Done when

- [ ] Every state a volume can be in has a visible, distinct rendering.
- [ ] A running volume's log updates in the browser without a reload.
- [ ] A stale or missing status file shows a warning within one reconciler
      tick.
- [ ] Component tests cover the page states and the log viewer; the SPA is
      type-checked and linted in CI.
