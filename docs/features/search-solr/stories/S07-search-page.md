---
type: Product Backlog Item
id:
parent: 2811
title: A search page that lands on the line in the viewer
---

# S07 · A search page that lands on the line in the viewer

**Story.** As an archivist, I want a search box on the office network where I type a name or a place and get a list of hits with a snippet, the archive and volume, and a link that opens the page in the viewer with the line highlighted, so that discovery across everything transcribed is one screen.

## Why it matters

This is the feature's visible result. It can live in the campaign browser (same SPA, new route) so it needs no new deployment.

## What this delivers

- A `/search` route in the campaign browser: query box, facet sidebar (archive, series, year), result list with snippets, paging; each hit links to `uv.html#?manifest=…` with the line selected (U02's highlight).
- Keyboard-navigable, small-screen layout, component tests like the rest of the SPA (B22).

## Done when

- [ ] An archivist finds a known name in a test campaign on DEV and lands on the highlighted line within three clicks.
