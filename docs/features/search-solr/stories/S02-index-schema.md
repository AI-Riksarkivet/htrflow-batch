---
type: Product Backlog Item
id:
parent: 2811
title: "Define the index: what a document is and which fields it has"
---

# S02 · Define the index: what a document is and which fields it has

**Story.** As the team, we want a written index design — the unit of a search hit (line, region or page), the fields (text, reference code, volume, page number, line coordinates, pipeline id, model, confidence, dates), and the analysis chain for 17th–19th-century Swedish — so that ingest, search and the UI are built against one contract.

## Why it matters

The most consequential decision in search is what a "document" is. A line-level document gives exact highlighting in the viewer but multiplies the index size; a page-level document is compact but can only point at a page. Getting this wrong means a full reindex later.

## What this delivers

- A schema document and the matching Solr `managed-schema`/config: document unit, fields with types, stored vs indexed, the tokeniser and filters for historical Swedish (case, diacritics, common spelling variants), and what is a facet.
- A sample: one real volume's ALTO converted to index documents, reviewed by an archivist for "is this what a hit should show".

## Done when

- [ ] Schema checked into the repo with a sample document set; an archivist has approved the hit shape.
