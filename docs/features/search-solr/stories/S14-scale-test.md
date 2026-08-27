---
type: Product Backlog Item
id:
parent: 2811
title: Scale test at archive size
---

# S14 · Scale test at archive size

**Story.** As the product owner, I want to know that the index and the search service hold up at the size the archive will reach — millions of pages, tens of millions of lines — with query times and storage needs measured, so that sizing for production is a number, not a hope.

## Why it matters

Everything before this is validated on a handful of volumes. Solr sizing (shards, heap, storage) depends entirely on document count and field choices from S02; the archive-scale batch run (B16) produces the real data to test with.

## What this delivers

- A load test against the DEV index populated from the B16 campaign (or synthetic documents at the projected size): p95 query latency at concurrency, ingest throughput, index size on disk; the resulting sizing recorded for the production values.

## Done when

- [ ] Numbers published in the docs; production Solr sizing derived from them.
