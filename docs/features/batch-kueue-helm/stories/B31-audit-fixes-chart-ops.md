---
type: Product Backlog Item
id: 2860
parent: 2800
title: Audit fixes — the chart is safe to install and the bucket exposes only results
---

# B31 · Audit fixes — the chart is safe to install and the bucket exposes only results

**Story.** As the platform team, I want an install of the chart to expose
nothing it should not — no default credentials, no open registry, no pod
logs readable by the world — and every image pinned by digest, so that the
chart can go onto a shared cluster as it is.

## What was found (audit package A3)

- **X8 (high)** — the development stack exposed S3 with default
  credentials and an unauthenticated image registry on node ports;
  control-plane images were pinned by tag, not digest.
- **X14 (medium)** — the whole `status/` tree, including full pod logs,
  was world-readable.
- **X4 (high)** — any image from any registry could be named by a
  pipeline file (see also B11, B13).
- **X15 (medium)** — chart/values hygiene: no schema, integers rendered
  as strings, undocumented values, hand-applied resources outside the
  chart.

## What was done

- Values schema; digest-pinned control-plane images; integer values
  rendered as integers; every value documented.
- Restricted pod security for the viewer and registry pods, security
  headers on the viewer, **namespace default-deny network policy** with
  per-role allow-lists, reconciler egress carved to git + S3.
- **Bucket policy split**: results anonymous-read for the viewer;
  `status/attempts`, `validation`, `volumes.json` and logs credential-only.
- Image allow-list (`allowedImageRepos`) and model-revision requirement
  in the reconciler; optional Kyverno `verifyImages` policy in the chart
  (off by default — B13 turns it on).
- Model-cache PVC and warm-up owned by the chart; CronJob deadlines and
  Lease RBAC; PSA labels from values; `.env` for cluster-local constants.

## Done when

- [ ] `helm template` on defaults and on full values passes
      `kubeconform -strict` (B23).
- [ ] No default credential and no unauthenticated write endpoint is
      exposed outside the dev stack's documented caveats.
- [ ] An anonymous client can read a volume's results but not the status
      tree (verified against the PoC bucket).
