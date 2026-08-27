---
type: Product Backlog Item
id: 2865
parent: 2800
title: Deploy to the DEV cluster with Argo CD
---

# B12 · Deploy to the DEV cluster with Argo CD

**Story.** As the platform team, we want the batch system on the shared
**DEV cluster** installed and kept in sync by **Argo CD** from a
deployment repository — never by someone running `helm install` from a
laptop — so that what runs in DEV is always exactly what is committed,
anyone can see the diff between desired and actual state, and the same
mechanism carries the system to staging and production (B34).

## Why it matters

The batch system already practises GitOps for its *work*: the campaigns
repo says what to transcribe and the reconciler makes it so (B04). This
story applies the same principle to the *platform itself*: a deployment
repo says which chart version and which values run in DEV, and Argo CD
makes it so. Two loops, same idea:

| Loop | Source of truth | Who reconciles | What it controls |
|---|---|---|---|
| Platform | deployment repo: chart version + `envs/dev/values.yaml` | Argo CD | which version of the batch system runs, with which settings, in which environment |
| Work | campaigns repo: `campaigns/*.yaml`, `pipelines/*.yaml` | the reconciler | which volumes are transcribed with which model |

Everything so far runs on one single-node GPU box the team owns outright:
no other tenants, no admission policies, no ingress controller, nothing
enforced by anyone else. DEV is where we find out what a shared cluster
rejects while a mistake still costs nothing.

## What this delivers

- **A deployment repository** (`htrflow-batch-deploy` or the platform
  team's existing one) with one folder per environment —
  `envs/dev`, `envs/staging`, `envs/prod` — each holding a values file
  and the pinned chart version. Governed like the campaigns repo (B11):
  protected `main`, reviewed pull requests.
- **An Argo CD `AppProject`** for htrflow-batch that limits *where* it may
  deploy (the batch namespace on DEV) and *from where* (the deployment
  repo and our chart/image registry) — the Argo CD side of "only our
  things run here".
- **An Argo CD `Application`** for DEV: automated sync, self-heal (a
  hand-edit on the cluster is reverted), prune, with the chart's CRD
  prerequisites handled in sync waves.
- **Prerequisites present on DEV**: Kueue, Kyverno, the NVIDIA runtime
  class and device plugin, an ingress controller, Argo CD itself and Kargo
  — each either provided by the platform team or declared as its own Argo
  CD application, and written down.
- **Signed images only** — which means the GPU wrapper image must be
  published through CI (the gap noted in B09), not built by hand.
- Access to the HCP bucket (B10) from DEV; a small real campaign run
  through the reconciler, visible in the status page and viewer at DEV's
  address.

## Done when

- [ ] `argocd app get htrflow-batch-dev` shows *Synced / Healthy* from the
      deployment repo; a manual change on the cluster is reverted within
      minutes.
- [ ] `security.allowedImageRepos` set, PSA `restricted`, network
      policies on; no image runs that did not come from our registry.
- [ ] A campaign completes; results open in the viewer from an office
      laptop with no tunnel (the viewer's half is U05).
- [ ] Everything DEV rejected is either fixed in the chart or recorded as
      a staging/production prerequisite.
- [ ] The DEV values file, the `AppProject` and the install steps are in
      the docs.

## Assumptions to confirm

- Argo CD and Kargo are (or will be) provided on the DEV cluster by the
  platform team; if not, installing them is a prerequisite task here.
- Staging exists as a separate namespace or cluster shaped like
  production; if it does not, creating it belongs to B34.
