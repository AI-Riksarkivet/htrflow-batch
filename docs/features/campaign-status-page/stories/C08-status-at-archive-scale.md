---
type: Product Backlog Item
id:
parent: 2923
title: Status at archive scale
---

# C08 · Status at archive scale

**Story.** As the operator, I want the status data split so that the page loads fast and the reconciler writes little, even with hundreds of thousands of volumes, so that the "no backend, two files" design survives archive scale instead of collapsing under one giant file.

## Why it matters

One `status.json` for everything is simple and will not scale: the reconciler rewrites it every tick and the browser parses all of it to show one campaign. The contract must change before the first archive-scale campaign (B16), not after.

## What this delivers

- A status layout with an index file (campaigns, counts, freshness) plus one status file per campaign, written only when that campaign changed; the page loads the index, then campaigns on demand; the exporter (B40) reads the same layout.
- The reconciler/frontend contract test (B21) extended to the new layout; a migration for existing status files; documented in the S3 Layout page.

## Done when

- [ ] On DEV with a synthetic status of 100 000 volumes the index loads in under a second and a campaign page in under two; the reconciler's per-tick write volume is proportional to what changed, not to the total.
