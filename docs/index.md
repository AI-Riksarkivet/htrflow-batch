# htrflow-batch

Kueue-gated batch HTR platform around the htrflow image — streaming
per-page results to S3 with IIIF viewer output.

## What this is

- Run HTRflow batch transcription of archival volumes on Kubernetes, using
  the **stock upstream htrflow image** as the base — upstream stays
  unmodified.
- **Kueue** owns queueing and GPU quota. No custom scheduler or orchestrator
  logic.
- Job status is the system of record. **No database.**
- Phase 1 measures its own overhead (fetch time vs GPU time) so Phase 2 is a
  data-driven decision, not an architectural enthusiasm.

The design is split into two phases. **Phase 1 (the PoC): the simple
design** — Kueue + Jobs + a wrapper that fetches directly from IIIF.
**Phase 2 (optimization, evidence-gated): the cache/data layer** — built
only if Phase 1's measurements justify it.

Non-goals for Phase 1: no cache/data layer, no submitter API, no
intra-volume sharding, no preemption/cohorts, no Prometheus metrics, no
rask integration.

## Current status

The Phase 1 PoC was validated end to end on bare k3s (2026-07-27/28):
Kueue gating, volume-per-Job lifecycle, streaming per-page upload, resume
after kill, the verify-gate completion contract, and the GPU path (41×
over CPU) all passed, and the UV4 viewer serves ALTO/text overlays live in
a real browser. Kueue-contention behavior under more than one concurrent
GPU Job, the `htrq` CLI, priority lanes, and NetworkPolicy remain open;
an archive-scale campaign is the pending next step.

## Where to go next

- [Getting Started](getting-started/index.md) — prerequisites and how to
  deploy and run a volume.
- [How it Works](how-it-works/architecture.md) — architecture, the
  streaming wrapper, and the decision log.
- [Campaigns (GitOps)](how-it-works/campaigns.md) — declare volumes in git,
  let the reconciler submit them, watch progress on the campaign browser.
- [Roadmap](roadmap/evolution.md) — the evidence-gated Phase 2 cache
  layer and what's still open.
