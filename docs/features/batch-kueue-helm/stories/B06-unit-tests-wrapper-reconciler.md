---
type: Product Backlog Item
id: 2847
parent: 2800
title: Unit tests for the wrapper and the reconciler
---

# B06 · Unit tests for the wrapper and the reconciler

**Story.** As a developer, I want the two Python components — the wrapper
that drives `htrflow` and the reconciler that submits work — covered by
fast tests that run in seconds without a GPU, a cluster or S3, so that I
can change either with confidence and CI can refuse a change that breaks
them.

## What this delivers

- **Wrapper**: manifest parsing (both IIIF versions, size fallbacks),
  download acceptance and byte caps, the streaming loop (starvation
  accounting, per-page failure, rolling delete, upload outages), the
  resume diff, the verification gate, every exit code including SIGTERM,
  log shipping.
- **Reconciler**: YAML parsing and its rules (ids, image allow-list, model
  revisions), state derivation for every volume state, the full tick
  against a fake bucket and fake cluster (lease, retries, sticky verdicts,
  warm-up gating, orphans, fairness), job rendering, the immutability
  guards, git checkout against a local repo.
- No wall-clock sleeps: tests inject clocks and events, so the suite is
  fast and deterministic.

## Done when

- [ ] `make test` runs both suites in seconds with no external services.
- [ ] Every exit code and every reconciler volume state has a test.
- [ ] The suite runs on every pull request and blocks merge on failure.
