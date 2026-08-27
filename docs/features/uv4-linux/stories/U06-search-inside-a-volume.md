---
type: Product Backlog Item
id: 2906
parent: 2801
title: Search inside a volume from the viewer
---

# U06 · Search inside a volume from the viewer

**Story.** As a reader, I want to type a word into the viewer and jump to
the pages and lines where it appears, so that I can find a name or a place
in a 500-page volume without reading it all.

## Why it matters

The viewer already has a search panel; it hides itself because no search
service is offered. A small service implementing the IIIF Content Search
standard over the volume's ALTO files would switch it on. The Solr feature
(#2811) may later provide this for the whole archive; this story is the
per-volume version that needs nothing but the files already in S3.

## What this delivers

- A minimal IIIF Content Search endpoint that answers queries for one
  volume from its ALTO files.
- The volume manifest advertising it, so the viewer's search panel appears.

Expected to be superseded by [S08](../../search-solr/stories/S08-iiif-content-search.md) in the Search feature, which backs the same panel with the cross-volume index.

## Done when

- [ ] Searching a term in the viewer lists hits and navigates to the page
      with the line highlighted.
- [ ] Decision recorded on whether this should be superseded by #2811.
