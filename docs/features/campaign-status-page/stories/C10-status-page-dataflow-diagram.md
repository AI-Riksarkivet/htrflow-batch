---
type: Product Backlog Item
id:
parent: 2923
title: Status page data-flow diagram
---

# C10 · Status page data-flow diagram

**Story.** As a reader of the Campaign Browser reference page, I want a diagram showing browser → viewer host → status index and per-campaign status files → `manifest.json` and run logs on the HCP → links into the viewer, so that it is clear where every number on the page comes from and what must be reachable for it to render.

## Why it matters

Every status-page incident so far has been a "which file, which origin, how stale" question; the picture makes it reviewable. Drawn as Mermaid per the conventions (B38).

## What this delivers

- One Mermaid diagram on the *Campaign Browser* reference page with the origins (viewer host, HCP), the files read (index, campaign status, manifest, log, thumbnail) and their freshness; a legend naming which are anonymous-read.

## Done when

- [ ] Renders on the docs site in both modes; C08 updates it when the layout changes.
