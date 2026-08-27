---
type: Product Backlog Item
id: 2902
parent: 2801
title: Open any batch-transcribed volume from S3 in the viewer
---

# U03 · Open any batch-transcribed volume from S3 in the viewer

**Story.** As anyone looking at the campaign status page, I want to click a
finished volume and have it open in the viewer with its transcription,
straight from S3, so that seeing a result never involves downloading files.

## Why it matters

This is the "show transcriptions from S3" half of the feature, and the
point where the batch feature and the viewer feature meet: the batch system
must write what the viewer reads, and the bucket must let a browser read it.

## What this delivers

- Every transcribed volume gets an **IIIF Presentation 3 manifest**
  (`iiif.json`) in S3 that points at the page images and at each page's
  ALTO file; the viewer understands it without any middle layer.
- One viewer deployment serves every volume: the address is simply
  `…/uv.html#?manifest=<the volume's iiif.json>`.
- The bucket policy lets browsers read results anonymously while keeping
  the status area and write access credentialed.
- The campaign browser links to the viewer per volume; the viewer is part
  of the Helm chart.

## Done when

- [ ] A volume finished by the batch system opens in the viewer from the
      status page link, images and text included, with no manual step.
- [ ] The same image and chart serve the proof-of-concept node (via ssh
      tunnel — see U05 for the production address).
