# htrflow-batch

Kueue-gated batch HTR platform around the htrflow image — streaming
per-page results to S3 with IIIF viewer output, driven from a campaigns
git repo.

## What this is

- Run HTRflow batch transcription of archival volumes on Kubernetes, using
  the **stock upstream htrflow image** as the base — upstream stays
  unmodified.
- **Kueue** owns queueing and GPU quota. No custom scheduler.
- **Git is the desired state, S3 is the observed state.** A campaign is a
  YAML file; a reconciler CronJob submits the missing Jobs every five
  minutes and publishes `status.json`; a read-only browser renders it.
  **No database.**
- Phase 1 measures its own overhead (fetch time vs GPU time) so Phase 2 is a
  data-driven decision, not an architectural enthusiasm.

The design is split into two phases. **Phase 1 (the PoC): the simple
design** — Kueue + Jobs + a wrapper that fetches directly from IIIF, plus
the GitOps reconciler. **Phase 2 (optimization, evidence-gated): the
cache/data layer** — built only if Phase 1's measurements justify it.

Non-goals for Phase 1: no cache/data layer, no submitter API, no
intra-volume sharding, no preemption/cohorts, no Prometheus metrics, no
rask integration.

## Current status

The Phase 1 PoC was validated end to end on bare k3s (2026-07-27/28):
Kueue gating, volume-per-Job lifecycle, streaming per-page upload, resume
after kill, the verify-gate completion contract, and the GPU path (41×
over CPU) all passed, and the UV4 viewer serves ALTO/text overlays live in
a real browser. Since then the GitOps reconciler, the campaign browser with
a live run log, and the D14 pod hardening were built and are running on
the GB10 arm64 k3s node (2026-08-25/26). A repository audit on 2026-08-26
was remediated in the same branch ([audit report](audits/2026-08-26-repo-audit.md)).
Kueue contention under more than one concurrent GPU Job, priority lanes, a
durable results bucket and an archive-scale campaign remain open
([Open Items](roadmap/open-items.md)).

## Where to go next

- [Getting Started](getting-started/index.md) — prerequisites, how to
  deploy, and how to run a campaign.
- [How it Works](how-it-works/architecture.md) — architecture, the
  streaming wrapper, failure handling, and the decision log.
- [Campaigns (GitOps)](how-it-works/campaigns.md) — declare volumes in git,
  let the reconciler submit them, watch progress on the campaign browser.
- [Reference](reference/index.md) — env contracts, YAML schemas, chart
  values, and the S3/status.json layout.
- [Development](development/index.md) — setup, tests, CI, security, the
  local k3s loop.
- [Roadmap](roadmap/evolution.md) — the evidence-gated Phase 2 cache
  layer and what's still open.
