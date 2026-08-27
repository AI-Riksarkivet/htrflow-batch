---
type: Product Backlog Item
id:
parent: 2811
title: IIIF Content Search backed by the index (search inside the viewer)
---

# S08 · IIIF Content Search backed by the index (search inside the viewer)

**Story.** As a reader with a volume open in the viewer, I want to search inside that volume from the viewer's own search panel, so that finding a word in a 500-page volume does not mean leaving the page.

## Why it matters

UV4 already has a search panel that hides itself because no search service is advertised. The IIIF Content Search API is the standard it expects; with the index in place, a shim that scopes a query to one volume and returns IIIF annotations is small. This supersedes U06, which sketched the same thing without an index.

## What this delivers

- An IIIF Content Search 1.0 endpoint on the search service (S06) scoped by volume, returning annotations with the line coordinates from the index; `iiif.json` advertising it as the manifest's search service.
- U06 closed in favour of this story.

## Done when

- [ ] The viewer's search panel appears for indexed volumes and navigates to hits with the line highlighted.
