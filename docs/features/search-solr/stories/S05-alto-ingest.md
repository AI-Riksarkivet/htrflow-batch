---
type: Product Backlog Item
id:
parent: 2811
title: Ingest ALTO into the index, idempotently
---

# S05 · Ingest ALTO into the index, idempotently

**Story.** As the team, we want a job that turns a completed volume's ALTO files into index documents and loads them into Solr — safe to run twice, resumable, and reporting what it did — so that the index tracks the bucket without human involvement.

## Why it matters

This is the batch feature's pattern applied to search: a small, boring job in the chart, no database, everything derivable from the bucket.

## What this delivers

- An ingest CronJob in the chart: for each ready volume (S04), read the ALTO files from the HCP, convert per the schema (S02), upsert into Solr keyed by `pipeline/volume/page/line` so re-runs replace rather than duplicate, commit, and record the volume as indexed.
- Per-volume error containment (one bad ALTO does not stop the batch), a run log in the bucket like the wrapper's, unit tests on the converter against real ALTO fixtures.

## Done when

- [ ] A campaign completing on DEV appears in the index; running the ingest twice changes nothing; a volume with a corrupt ALTO is reported and the rest indexed.
