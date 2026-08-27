---
type: Product Backlog Item
id:
parent: 2811
title: Deploy Solr on the cluster through Argo CD
---

# S03 · Deploy Solr on the cluster through Argo CD

**Story.** As the platform team, we want Solr installed on DEV from the deployment repo by Argo CD — with persistent storage, backups and a resource budget — so that the search index is part of the same GitOps platform as the batch system and is promoted the same way (B34).

## Why it matters

A hand-installed Solr is a snowflake. The Solr Operator (or the Bitnami chart) installed as an Argo CD application gives the index the same lifecycle as everything else.

## What this delivers

- A Solr (SolrCloud or standalone, decided in S02 by index size) Argo CD application in the deployment repo, with a PVC on durable storage, a backup schedule to the HCP, and resource requests recorded.
- The schema from S02 applied as a config set from the repo, not edited in the admin UI.

## Done when

- [ ] Solr is *Synced / Healthy* on DEV from the deployment repo; the admin UI is reachable only as S12 allows; a backup has been restored once.
