---
type: Product Backlog Item
id:
parent: 2811
title: Access control and network policy for Solr and the search service
---

# S12 · Access control and network policy for Solr and the search service

**Story.** As the security owner, I want Solr reachable only by the ingest and the search service, the search service reachable only through the ingress with the same controls as the viewer, and both running as restricted pods from CI-built images, so that adding search does not open a hole in the batch system's trust boundary.

## Why it matters

Solr's admin API can delete everything; the search service is the first component that accepts free-text input from users. Both are new attack surface and NIS2 applies to them as to the rest.

## What this delivers

- Network policies: Solr accepts only the ingest and search-service pods; the search service accepts only the ingress; both egress-limited. Solr authentication enabled with credentials in a Secret; the admin UI not exposed.
- Both images in the inventory (B37) with signature, provenance, SBOM and Trivy; restricted PSA; input validation and size caps on the search service.

## Done when

- [ ] A pod outside the allow-list cannot reach Solr; the search service passes the same checks as the viewer; the images pass Kyverno `Enforce`.
