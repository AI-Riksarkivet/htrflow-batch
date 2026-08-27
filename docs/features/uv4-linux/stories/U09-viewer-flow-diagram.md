---
type: Product Backlog Item
id: 2908
parent: 2801
title: Viewer flow diagram
---

# U09 · Viewer flow diagram

**Story.** As a reader of the Viewing Results page, I want a diagram showing browser → status page → `iiif.json` on the HCP → UV4 → page images from IIIF plus ALTO overlays, so that it is clear which parts of a viewed page come from where — and therefore what must be reachable and CORS-enabled for it to work.

## Why it matters

Every viewer problem so far (tunnels, localhost rewriting, CORS, CSP) has been a "which origin serves what" problem; the picture makes that reviewable. Drawn as Mermaid per the conventions (B38).

## What this delivers

- One Mermaid diagram on the *Viewing Results* page with the four origins (viewer host, HCP, IIIF image server, status file) and the requests between them.
- A legend naming which origins need anonymous read and CORS.

## Done when

- [ ] Renders on the docs site in both modes; U05 references it for its ingress/CORS checklist.
