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
- Skriv om i Indexed-Job-termer och behåll ett kampanjnivå-facit över `failedIndexes` som överlever Jobbets TTL. (revision 2026-09-07, X19)
- Läs-API:t amorterar polling med en kort cache i stället för en list per request och kort. (revision 2026-09-07, X38)
- The read API lists the bucket (decision 2026-09-08): with the internal S3 access it gains for progress.json, it walks `<namespace>/<pipeline>/<volume>/` prefixes (a thousand per request, cached about a minute, one refresh per namespace) and reads a volume's `manifest.json` only for the page of volumes being shown. What exists in S3 becomes a page instead of a listing with credentials.
- The list merges three sources at read time: live Jobs (running state), the campaign ConfigMaps and their status ConfigMaps (which volumes belong to which campaign, who submitted, when it finished — B76), and S3 (what actually exists). A campaign whose Job the TTL removed still shows, finished with its date; a volume in S3 that belongs to no known campaign shows as an orphan, which is what B10's retention needs to act on.
- Capacity: 10 000 volumes in a namespace is ten list requests per refresh, 100 000 is a hundred; tens of thousands of campaigns are ordinary ConfigMap counts. Beyond that a real index owned by the read API is the next step, not a cache server.

## Done when

- [ ] On DEV with a synthetic status of 100 000 volumes the index loads in under a second and a campaign page in under two; the reconciler's per-tick write volume is proportional to what changed, not to the total.
