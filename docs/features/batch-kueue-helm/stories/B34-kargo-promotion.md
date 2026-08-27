---
type: Product Backlog Item
id: 2875
parent: 2800
title: Promote releases dev → staging → prod with Kargo
---

# B34 · Promote releases dev → staging → prod with Kargo

**Story.** As the product owner, I want a new version of the batch system
to reach production only by *promotion* — it runs in DEV, passes checks,
is promoted to staging, passes checks, and is then promoted to production
with an explicit approval — so that "what is in prod?" is always a
version that was proven in the two environments before it, and nobody
edits production by hand.

## Why it matters

Argo CD (B12) keeps each environment equal to its folder in the
deployment repo. What it does *not* do is move a version from one folder
to the next. Without a promotion tool that is a person editing
`envs/prod/values.yaml` — error-prone, unaudited, and easy to do out of
order. **Kargo** is the promotion layer built for Argo CD: it watches for
new releases, packages each as a unit called *Freight* (the exact image
digests + chart version + configuration), moves it through *Stages* in a
fixed order, and writes the change into the deployment repo so Argo CD
applies it. Each promotion is a commit with a name on it; production can
require a human approval; and a stage can only receive Freight that the
previous stage has verified.

## What this delivers

- **The Helm chart published as a versioned, signed OCI artifact** by the
  publish workflow, next to the images (today only images are published;
  Kargo needs a chart version to promote).
- **A Kargo `Project` with a `Warehouse`** subscribed to our image
  repositories (wrapper, reconciler, viewer) and the chart repository —
  only digests carrying our CI signature become Freight.
- **Three `Stages`: `dev` → `staging` → `prod`**, each promoting by
  updating the corresponding `envs/<stage>/values.yaml` in the deployment
  repo (Kargo's `git-*`/`helm-update` promotion steps, via a pull request
  or direct commit as the governance in B11 allows) and waiting for Argo
  CD to report *Synced / Healthy*.
- **Verification per stage** before Freight may move on: Argo CD health
  plus a smoke check — a one-volume campaign submitted through the
  reconciler completes and its `manifest.json` appears — expressed as an
  `AnalysisTemplate` (Argo Rollouts) that Kargo runs.
- **Auto-promotion to `dev`**, **auto-promotion to `staging`** after DEV
  verification, **manual approval to `prod`** by a named role.
- The chart's per-environment differences (ingress hostname, bucket,
  quota, Kyverno identity) confined to the values files, so promotion
  never touches the chart.

## Done when

- [ ] A new release published by CI appears as Freight and reaches DEV
      with no human action.
- [ ] Freight cannot be promoted to `staging` before DEV verification
      passes, nor to `prod` without approval; an attempt to skip a stage
      is refused.
- [ ] Every promotion is a commit in the deployment repo naming the
      Freight (image digests + chart version) and who promoted it.
- [ ] Rolling production back is promoting the previous Freight, and is
      tested once.
- [ ] Documented for operators: how to see what is where, promote, and
      roll back.
