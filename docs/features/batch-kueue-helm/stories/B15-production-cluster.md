---
type: Product Backlog Item
id: 2894
parent: 2800
title: Production stage — first promotion and tuning
---

# B15 · Production stage — first promotion and tuning

**Story.** As the platform team, we want the first version of the batch
system in production to arrive the same way every later one will — as a
Kargo promotion from staging, approved by a named person, applied by Argo
CD — and then be tuned for production's GPUs, so that the first
archive-scale campaign runs on a system whose every control and every
path to production has already been exercised.

## Why it matters

If the only differences between staging and production are one values
file, then going to production is a promotion and a review of that diff.
Every number in the chart (GPU quota, memory per job, page width) was
chosen for the single-node PoC and needs re-checking on the real
hardware.

## What this delivers

- `envs/prod/values.yaml` in the deployment repo with the diff from
  staging reviewed: ingress hostname and TLS, HCP bucket and credentials,
  GPU quota, memory, the Kyverno identity for the signature policy.
- The production Argo CD `Application` and Kargo `prod` stage (B34)
  targeting the production cluster; the first promotion approved and
  recorded.
- Kyverno in `Enforce`, PSA `restricted`, network policies on, image
  allow-list set — identical to DEV and staging, no exceptions added for
  production.
- Quota, memory and page-width values tuned on the production GPUs and
  recorded with their rationale — as a promoted change, not a hand edit.
- Who is on call for it, and where the runbook lives (the Failure
  Handling and Campaigns pages are the runbook today).

## Done when

- [ ] Production is *Synced / Healthy* from the deployment repo and
      received its version through a Kargo promotion with approval.
- [ ] A campaign of a few volumes runs; status page and a finished volume
      open at the production address.
- [ ] Tuned values and their rationale are in the docs; the operational
      owner is named.
- [ ] B33 (second audit) closed with no open high finding.
