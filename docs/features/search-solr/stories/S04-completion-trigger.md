---
type: Product Backlog Item
id:
parent: 2811
title: Know when a volume is ready to index
---

# S04 · Know when a volume is ready to index

**Story.** As the ingest, I want a reliable signal that a volume is complete and its ALTO is final, so that a volume is indexed exactly once, never half-transcribed, and never missed.

## Why it matters

The batch system already has this signal: `manifest.json` is written last, only after every page is verified. The question is how the ingest learns about it — polling the bucket for new markers with a watermark, the reconciler recording "indexed" in its status, or object notifications from the HCP if it offers them.

## What this delivers

- A decision among: (a) the ingest job scans for `manifest.json` newer than its watermark each tick; (b) the reconciler emits a per-volume "done, not indexed" state that the ingest consumes and clears; (c) HCP event notifications. Recommended default: (a), because it needs nothing new in the batch system and is rebuildable from the bucket alone.
- The chosen mechanism implemented with a persisted watermark in the bucket (`status/index.json`) and exposed as a metric (B40).

## Done when

- [ ] A volume completing on DEV is picked up within one ingest interval; a re-run of the same volume under a new pipeline is detected as a new version, not a duplicate.
