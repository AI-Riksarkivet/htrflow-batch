---
type: Feature
id: 2811
parent: 2769
title: Solr
---

# Feature: Search — Solr (#2811)

!!! warning "Draft — not yet reviewed"

    These stories are a first proposal and have not been reviewed by the
    product owner. They are not in Azure DevOps yet; they will be created
    there once reviewed.

## In one paragraph

The batch feature produces transcriptions; the viewer lets a person read
one volume. Neither lets anyone *find* anything. This feature makes the
transcribed text **searchable across every volume**: an index (Apache
Solr) fed automatically from the results bucket as volumes complete, a
search service in front of it, and a search page — so that an archivist
or a researcher can type a name or a place and land on the page and the
line where it appears, in a viewer that highlights it.

## Why we are building it

- **Transcription is only the means.** The value of HTR at archive scale
  is discovery: finding the one page in a million that mentions a person.
- **The results already exist in the right shape.** Every finished volume
  has an ALTO file per page with the text *and the position of every
  line*, and a completion marker that says the volume is whole. An index
  built from those can point a hit straight to a line in the viewer.
- **Fed by the same machinery.** New volumes are indexed the way they are
  transcribed — automatically, from the bucket, with no one copying files.

## What "done" looks like for the feature

A search page on the office network where a query returns hits across
every completed volume, each hit opening the page in the viewer with the
line highlighted, with the index kept current as campaigns complete and
rebuilt from the bucket alone if lost.

## Stories

### Not started — in order

| Id | Story |
|---|---|
| [S02](stories/S02-index-schema.md) | Define the index: what a document is and which fields it has |
| [S03](stories/S03-solr-deployment.md) | Deploy Solr on the cluster through Argo CD |
| [S04](stories/S04-completion-trigger.md) | Know when a volume is ready to index |
| [S05](stories/S05-alto-ingest.md) | Ingest ALTO into the index, idempotently |
| [S06](stories/S06-search-api.md) | A search service with highlighting and facets |
| [S07](stories/S07-search-page.md) | A search page that lands on the line in the viewer |
| [S08](stories/S08-iiif-content-search.md) | IIIF Content Search backed by the index (search inside the viewer) |
| [S09](stories/S09-archival-context.md) | Archival context on every hit — archive, series, volume, year |
| [S10](stories/S10-htr-error-tolerance.md) | Tolerate HTR errors — fuzzy and variant matching evaluated |
| [S11](stories/S11-confidence-in-index.md) | Confidence scores in the index |
| [S12](stories/S12-access-and-security.md) | Access control and network policy for Solr and the search service |
| [S13](stories/S13-reindex-and-lifecycle.md) | Rebuild, re-run and removal — the index lifecycle |
| [S14](stories/S14-scale-test.md) | Scale test at archive size |

Suggested order: S02 is the design decision and gates everything else; S03–S05
make an index exist and stay current; S06–S08 make it usable; S09–S11
make it good; S12–S14 make it operable. Story ids are stable identifiers,
never renumbered (S01 was removed and its number retired); a new story
takes the next free number.
