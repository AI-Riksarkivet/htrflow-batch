---
type: Product Backlog Item
id:
parent: 2811
title: Archival context on every hit — archive, series, volume, year
---

# S09 · Archival context on every hit — archive, series, volume, year

**Story.** As a researcher, I want every hit to say which archive, series and volume it comes from and what years the volume covers — not just a reference code — so that a hit can be judged without opening it and results can be narrowed by archive or period.

## Why it matters

The batch system knows a volume only by its reference code and IIIF manifest. The archival hierarchy and dates live in the National Archival Database (NAD); joining them at ingest turns codes into context and makes the facets in S06 meaningful.

## What this delivers

- A lookup at ingest (S05) from reference code to archive / series / volume title / date range via the NAD API (or a cached extract), stored as facet fields; a fallback that indexes without context and flags the volume when the lookup fails.
- The mapping cached in the bucket so a reindex does not re-query NAD for every volume.

## Done when

- [ ] Hits on DEV show archive, series, title and years; the facets narrow results; unmatched reference codes are listed in the ingest log.
