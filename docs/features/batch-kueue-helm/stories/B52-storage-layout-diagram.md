---
type: Product Backlog Item
id: 2886
parent: 2800
title: Storage layout diagram
---

# B52 · Storage layout diagram

**Story.** As a reader of the S3 Layout page, I want a diagram showing the HCP bucket: per-pipeline/volume results, `status/`, what is anonymous-read versus credentialed, which files the viewer, the browser, the reconciler and the exporter each read, so that the page can be understood from the picture first and the text second.

## Why it matters

The bucket policy split is the viewer's security model and nobody can see it today. Drawn as Mermaid per the conventions (B38) and placed on the page it explains.

## What this delivers

- One Mermaid diagram on the *S3 Layout* page showing the HCP bucket: per-pipeline/volume results, `status/`, what is anonymous-read versus credentialed, which files the viewer, the browser, the reconciler and the exporter each read.
- A three-line legend under it; the diagram referenced from the feature page where relevant.

## Done when

- [ ] Renders on the docs site in light and dark mode on the *S3 Layout* page.
- [ ] Follows the conventions page; reviewed by someone who did not draw it.
