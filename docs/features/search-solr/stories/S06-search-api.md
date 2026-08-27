---
type: Product Backlog Item
id:
parent: 2811
title: A search service with highlighting and facets
---

# S06 · A search service with highlighting and facets

**Story.** As a client — the search page, the viewer, or another system such as the MCP server — I want one HTTP endpoint that takes a query and returns hits with highlighted snippets, line coordinates, links into the viewer and facet counts, so that Solr itself is never exposed and every client gets the same answer.

## Why it matters

Solr's own API is too powerful to expose (it can delete the index) and too raw for clients (they would each reimplement highlighting and link building). A thin service owns the query contract.

## What this delivers

- A small stateless service in the chart: `GET /search?q=…&facets=…&page=…` returning hits (volume, page, line, snippet with highlight, coordinates, viewer URL), facets (archive, series, year, pipeline), paging; OpenAPI described.
- Query building and highlighting tested against the S02 sample; rate limiting and a request size cap.

## Done when

- [ ] A query on DEV returns hits whose viewer link opens the right page with the line highlighted; the OpenAPI document is published in the docs.
