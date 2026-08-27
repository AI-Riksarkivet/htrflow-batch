---
type: Product Backlog Item
id:
parent: 2811
title: Confidence scores in the index
---

# S11 · Confidence scores in the index

**Story.** As a researcher, I want to see how confident the HTR was about a hit, and optionally to exclude low-confidence lines, so that I can tell a solid hit from a guess.

## Why it matters

Feature #2770 adds a per-page quality prediction to the ALTO; per-line confidence is already there. Indexing both makes confidence a filter and a sort key and gives the archive its first view of where transcription quality is weakest.

## What this delivers

- Line and page confidence fields in the schema (S02), ingested from ALTO (S05), exposed as a filter and shown per hit in S06/S07.

## Done when

- [ ] A search on DEV can be limited to hits above a confidence threshold; the value is shown per hit.
