---
type: Product Backlog Item
id:
parent: 2923
title: Volume detail page
---

# C05 · Volume detail page

**Story.** As an archivist checking a result, I want a page per volume with a grid of its pages — thumbnail, status, confidence and timing per page — with the run log as a tab and a link into the viewer at any page, so that I can judge a volume's quality and jump to the weak pages.

## Why it matters

The run-log viewer already shows a per-page grid of timings from `manifest.json`; the page-level quality score from feature #2770 (B20) and per-page thumbnails make it the natural place to review a volume, not just debug it.

## What this delivers

- A `/volume/<pipeline>/<id>` route: page grid with thumbnail (the wrapper's `thumb.jpg` for page 1 today — a small per-page thumbnail is a wrapper change to agree with B20), status, confidence colour, seconds; the log viewer as a tab; "open in viewer" per page (`uv.html#?manifest=…&cv=<page>`).
- Sorting the grid by confidence, so the weakest pages come first.

## Done when

- [ ] A finished volume on DEV shows the grid with confidence once B20 is in; clicking a page opens the viewer on that page.
