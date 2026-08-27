---
type: Product Backlog Item
id: 2844
parent: 2800
title: Install the whole system with one Helm command, hardened by default
---

# B03 · Install the whole system with one Helm command, hardened by default

**Story.** As a platform operator, I want to install or upgrade the batch
system on a cluster with a single `helm install`, and I want it to be safe
by default — locked-down pods, no network access it doesn't need, images we
have verified — so that putting it on a shared production cluster is not a
security review in itself.

## Why it matters

A system made of eight or nine Kubernetes objects is only operable if they
ship together, with the right defaults, and upgrade as a unit. And a GPU
workload that downloads models from the internet and writes to S3 is exactly
the kind of thing a security team asks hard questions about.

## What this delivers

- **One chart** (`charts/htrflow-batch`) containing the queue, the
  reconciler, the viewer, the model cache and all their wiring. Every setting
  is a documented value with a schema, so a typo fails at install, not at
  3 a.m.
- **Models are pre-warmed into a read-only cache**; the GPU jobs run fully
  offline. No job ever talks to the internet.
- **Restricted pod security** across the board: non-root, read-only
  filesystem, no capabilities, no service-account tokens.
- **Default-deny networking** in the namespace, with a short allow-list per
  role (jobs → IIIF and S3 only; reconciler → git and S3 only).
- **Images pinned by digest**, and an optional policy that refuses to run
  any image that isn't signed by our CI.
- Upgrade notes and a changelog in the chart README.

## Done when

- [ ] `helm install` on a fresh cluster with Kueue and an S3 secret in place
      brings up a working system; `helm upgrade` is documented.
- [ ] `helm lint`, template rendering and Kubernetes schema validation pass
      in CI.
- [ ] Jobs run with no internet egress and no PSA "restricted" warnings.
- [ ] All control-plane images are referenced by digest.
