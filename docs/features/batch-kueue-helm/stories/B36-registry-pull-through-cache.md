---
type: Product Backlog Item
id: 2873
parent: 2800
title: A local registry as the single, cached source of images
---

# B36 · A local registry as the single, cached source of images

**Story.** As the platform team, we want every image the cluster runs to
come from **one registry we operate** — which fetches from Docker Hub or
GitHub on first use and serves from its own copy after that — so that the
cluster has exactly one place images come from, keeps working when the
internet or an upstream registry does not, and never hits upstream rate
limits during a campaign.

## Why it matters

Today the PoC has a bare in-cluster registry for hand-pushed images while
control-plane images are pulled straight from Docker Hub. Three problems:
every node pulls from the internet (and Docker Hub throttles anonymous
pulls); an upstream outage or a deleted tag stops the cluster; and the
image allow-list (B13) has to name *external* registries. A
**pull-through cache** — a registry that proxies an upstream and stores
what it has served — fixes all three, and gives us the one place where
models can live too (B35).

## What this delivers

- **Harbor** (or the platform team's equivalent) with **proxy-cache
  projects** for the upstreams we use — Docker Hub (`docker.io`) and
  GitHub Container Registry — and a normal project for what we push
  ourselves (chart, images, models). Harbor is preferred over the bare
  `registry:2` because it also gives RBAC, robot accounts, retention and
  replication, hosts OCI model artifacts, and keeps cosign signatures as
  accessories next to the image. (Note from the 2026-06 spike: official
  Harbor images are amd64-only; on the arm64 PoC node they need emulation
  and a native redis. Irrelevant on DEV/prod if those are amd64.)
- Chart values and pipeline files pointing at the local registry for
  *everything*; `security.allowedImageRepos` reduced to that one prefix.
- **Signature and attestation reachability verified, not assumed**: the
  Kyverno policy (B13) fetches the cosign signature and the SLSA
  provenance from the registry it pulls the image from, so the proxy must
  serve those referrers too. Test: pull a CI-signed image through the
  cache and verify it against the cache with `cosign verify`; if the proxy
  drops referrers, mirror our own images into the registry (Harbor
  replication) instead of proxying them.
- Digest-pinned references resolve identically through the cache (a proxy
  never changes a digest); the Kargo Warehouse (B34) subscribes to the
  local registry.
- Retention and garbage collection sized for GPU wrapper images (~10 GB
  each) and documented; the cluster's egress policy allows the registry
  pods, and only them, to reach the upstreams.

## Done when

- [ ] Every pod in the batch namespace runs an image whose reference
      starts with the local registry; the allow-list contains only that
      prefix.
- [ ] With upstream access blocked, the chart still installs and a
      campaign still runs from cached images.
- [ ] A CI-signed image pulled through the cache passes Kyverno
      `verifyImages` in `Enforce`; the test is recorded.
- [ ] Retention/GC settings and the upstream allow-list are in the docs.

- [ ] The Security → Trust boundary table gains a registry row; the chart reference documents the registry values.
