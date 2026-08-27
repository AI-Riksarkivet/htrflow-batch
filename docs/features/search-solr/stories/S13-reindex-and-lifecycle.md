---
type: Product Backlog Item
id:
parent: 2811
title: Rebuild, re-run and removal — the index lifecycle
---

# S13 · Rebuild, re-run and removal — the index lifecycle

**Story.** As the operator, I want to rebuild the whole index from the bucket alone, replace a volume's entries when it is re-transcribed with a newer pipeline, and remove entries for volumes withdrawn from the campaigns repo, so that the index is never the only copy of anything and never shows results that no longer exist.

## Why it matters

Following the batch system's principle — the bucket is the truth, everything else is derived — the index must be disposable. Re-runs under a new pipeline id and orphaned volumes are already states the reconciler knows.

## What this delivers

- A full-reindex mode of the ingest job (S05) that ignores the watermark; a documented, tested procedure and its duration on DEV.
- Versioning rule: when a volume completes under a newer pipeline, the older pipeline's documents for it are deleted (or kept and marked superseded — decided in S02); orphaned volumes removed on the next ingest.

## Done when

- [ ] A full reindex on DEV reproduces the same document count; a re-run replaces; an orphan disappears from search within one ingest interval.
