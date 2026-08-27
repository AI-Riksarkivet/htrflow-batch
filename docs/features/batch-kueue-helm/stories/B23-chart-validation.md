---
type: Product Backlog Item
id: 2853
parent: 2800
title: The Helm chart is validated on every change
---

# B23 · The Helm chart is validated on every change

**Story.** As the platform team, we want every change to the chart
linted, rendered with both default and fully-populated values, and checked
against the Kubernetes API schema, so that a typo in a template is caught
in CI rather than by a failed `helm upgrade` on the cluster.

## What this delivers

- `helm lint` on the chart.
- A **values schema** (`values.schema.json`) so an unknown or mistyped
  value fails at install time.
- A render on the defaults *and* on a "everything switched on" values
  file (`charts/htrflow-batch/ci/full-values.yaml`) — the only way to exercise the optional
  templates such as the Kyverno policy and network rules.
- **`kubeconform -strict`** on the rendered manifests against the
  Kubernetes schemas.

## Done when

- [ ] All four checks run in CI (`dagger call check-chart`) and locally
      (`make helm-lint helm-template`).
- [ ] A template that renders an invalid Kubernetes object fails the
      pull request.
