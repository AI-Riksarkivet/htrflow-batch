---
type: Product Backlog Item
id:
parent: 2923
title: Download a volume's results
---

# C06 · Download a volume's results

**Story.** As a data scientist or a downstream system, I want to download a finished volume's ALTO (and PAGE) files as one bundle from the page, so that results can be taken into other tools without knowing the bucket layout.

## Why it matters

Results are in the bucket, anonymous-read per volume, but as hundreds of files under a prefix. A bundle is the difference between "available" and "usable" when the next step is a notebook or another tool.

## What this delivers

- A download action on the volume page and in the volume table that fetches the volume's ALTO/PAGE files client-side and zips them in the browser (no backend), with `manifest.json` included for provenance; or, if volumes are too large for that, a wrapper change that writes `results.zip` next to `manifest.json` at completion.
- The choice recorded with the measured size of a 500-page volume.

## Done when

- [ ] A finished volume downloads as one zip containing every page's ALTO and PAGE plus the manifest; verified against the bucket listing.
