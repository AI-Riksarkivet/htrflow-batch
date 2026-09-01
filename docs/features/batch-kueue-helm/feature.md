---
type: Feature
id: 2800
parent: 2769
title: Batch using Kueue+Helm chart
---

# Feature: Batch using Kueue+Helm chart (#2800)

## In one paragraph

Today, transcribing an archive volume with `htrflow` is something a person
starts by hand, watches, and copies the results from. This feature turns it
into a **batch service**: someone lists the volumes they want in a file,
commits it, and the cluster works through them on its GPUs — queued fairly,
restarted automatically when something goes wrong, and with every finished
page landing in S3 the moment it is transcribed. A web page shows what is
queued, running and done. Nothing needs babysitting, and the whole system
installs with one Helm command.

## Why we are building it

- **Scale.** The archive has hundreds of thousands of volumes. Hand-run
  transcription does not get us there; a queue that keeps the GPUs busy
  around the clock does.
- **Trust.** Every finished volume carries a record of exactly which model,
  which image and which settings produced it, and the system verifies that
  every page it promised is actually in S3 before it calls a volume done.
- **Compliance we can evidence.** As a public-administration entity
  Riksarkivet is in scope of NIS2; the signed, provenance-attested images
  with SBOMs and the cluster policy that enforces them (B09, B13, B14) are
  the supply-chain and vulnerability-handling controls the directive asks
  for, in a form an auditor can verify rather than read about.
- **Low operating cost.** No custom scheduler, no database, no service that
  has to be kept alive. The pieces are standard Kubernetes objects; if the
  cluster is up, the batch system is up.

## What "done" looks like for the feature

The feature is complete when an archive-scale campaign (thousands of pages
across many volumes) has run unattended on a production cluster, into
the HCP, with the cluster refusing to run any image we did not
build — and we have the throughput and GPU-utilisation numbers from that
run. Everything needed to *run* such a campaign exists today on the
proof-of-concept node; the open stories are about taking it through the
dev cluster to production, switching the supply-chain controls on, and
running it at scale.

## Stories

Story ids are stable identifiers, not a sequence: a number is never reused
or renumbered, and a story added later simply takes the next free number.
Each story is one deliverable; where a story is naturally a list that will
grow (images, diagrams, documentation pages, dashboards) there is one story
per item and a small parent story for the list itself. Reading order is the
tables below.

### Implemented in the repository — awaiting acceptance

| Id | Story |
|---|---|
| [B01](stories/B01-streaming-wrapper.md) | Transcribe a volume page-by-page, streaming results to S3 |
| [B02](stories/B02-kueue-job-contract.md) | Queue volumes fairly on the GPUs and fail safely |
| [B03](stories/B03-helm-chart.md) | Install the whole system with one Helm command, hardened by default |
| [B04](stories/B04-gitops-campaigns.md) | Declare campaigns in git and let the system run them (GitOps) |
| [B06](stories/B06-unit-tests-wrapper-reconciler.md) | Unit tests for the wrapper and the reconciler |
| [B07](stories/B07-local-dev-and-poc.md) | Run the whole thing locally and on the GPU proof-of-concept node |
| [B08](stories/B08-documentation-site.md) | Documentation an operator can deploy and run from |
| [B09](stories/B09-signed-releases-slsa.md) | Signed releases with SLSA provenance and a software bill of materials |
| [B21](stories/B21-contract-tests.md) | Contract tests between the wrapper, the reconciler and the browser |
| [B23](stories/B23-chart-validation.md) | The Helm chart is validated on every change |
| [B24](stories/B24-lint-format-typecheck.md) | Lint, formatting and type checks from locked tool versions |
| [B25](stories/B25-htrflow-api-pin-test.md) | A canary test for htrflow version bumps |
| [B27](stories/B27-one-pipeline-local-and-ci.md) | The same checks run locally and in CI |
| [B28](stories/B28-repository-audit.md) | Independent repository audit (2026-08-26) |
| [B29](stories/B29-audit-fixes-reconciler.md) | Audit fixes — the reconciler remembers, scales and retries correctly |
| [B30](stories/B30-audit-fixes-wrapper.md) | Audit fixes — the wrapper never loses a page or misjudges a failure |
| [B31](stories/B31-audit-fixes-chart-ops.md) | Audit fixes — the chart is safe to install and the bucket exposes only results |
| [B44](stories/B44-wrapper-cpu-image.md) | Wrapper (CPU) image — CI build, SLSA provenance, SBOM and Trivy scan |
| [B45](stories/B45-reconciler-image.md) | Reconciler image — CI build, SLSA provenance, SBOM and Trivy scan |

### Not started — productionalisation, in order

| Id | Story |
|---|---|
| [B10](stories/B10-durable-results-bucket.md) | Store results on the HCP |
| [B11](stories/B11-campaigns-repo-governance.md) | Govern the campaigns repo — protected main, reviewed pull requests |
| [B12](stories/B12-dev-cluster.md) | Deploy to the DEV cluster with Argo CD |
| [B13](stories/B13-policy-as-code-kyverno.md) | Only images we built may run — policy as code with Kyverno |
| [B14](stories/B14-slsa-level-and-provenance-verification.md) | Raise the SLSA level and verify provenance, not just signatures |
| [B26](stories/B26-dependency-updates.md) | Automatic dependency updates with Dependabot |
| [B37](stories/B37-every-image-reproducible-slsa-trivy.md) | Image inventory — no image runs that CI did not build |
| [B42](stories/B42-htrflow-arm64-base-image.md) | `htrflow` base image for arm64 published and pinned by digest |
| [B61](stories/B61-htrflow-image-org-namespace.md) | htrflow-imagen publiceras under riksarkivet/ på Docker Hub, inte airiksarkivet/ |
| [B62](stories/B62-eupl-license.md) | htrflow-batch licensieras under EUPL-1.2, samma som htrflow |
| [B63](stories/B63-campaigns-as-indexed-jobs.md) | Kampanjer körs som Kubernetes Indexed Jobs — reconcilern och dess statusfiler tas bort |
| [B41](stories/B41-gpu-wrapper-image-in-ci.md) | GPU wrapper image (arm64) built in CI with SLSA provenance and a Trivy scan |
| [B43](stories/B43-model-packaging-job-image.md) | Model-packaging job image built in CI with SLSA provenance and a Trivy scan |
| [B36](stories/B36-registry-pull-through-cache.md) | A local registry as the single, cached source of images |
| [B35](stories/B35-models-as-signed-oci-artifacts.md) | Models as signed OCI artifacts in our registry (ModelPack) |
| [B34](stories/B34-kargo-promotion.md) | Promote releases dev → staging → prod with Kargo |
| [B40](stories/B40-metrics-endpoint-grafana.md) | Metrics exporter — the batch system's numbers in Prometheus format |
| [B59](stories/B59-grafana-dashboard-as-code.md) | Grafana dashboard as code |
| [B60](stories/B60-alert-rules-as-code.md) | Alert rules as code |
| [B38](stories/B38-architecture-diagrams.md) | Diagram conventions — how every architecture picture is drawn and kept |
| [B46](stories/B46-system-context-diagram.md) | System context diagram (C4 level 1) |
| [B47](stories/B47-runtime-containers-diagram.md) | Runtime containers diagram refreshed (C4 level 2) |
| [B48](stories/B48-two-gitops-loops-diagram.md) | The two GitOps loops diagram |
| [B49](stories/B49-environments-and-promotion-diagram.md) | Environments and promotion diagram |
| [B50](stories/B50-supply-chain-trust-boundary-diagram.md) | Supply chain and trust boundary diagram |
| [B51](stories/B51-network-diagram.md) | Network diagram |
| [B52](stories/B52-storage-layout-diagram.md) | Storage layout diagram |
| [B53](stories/B53-campaign-state-machine-diagram.md) | Campaign lifecycle state diagram |
| [B54](stories/B54-deployment-and-promotion-page.md) | Deployment & Promotion page |
| [B55](stories/B55-registry-and-models-page.md) | Registry & Models page |
| [B56](stories/B56-governance-page.md) | Governance page |
| [B57](stories/B57-operations-runbook-index.md) | Operations runbook index |
| [B58](stories/B58-docs-ci-gate.md) | Docs CI gate — broken links, missing nav entries and undocumented chart values fail the build |
| [B33](stories/B33-second-audit-before-production.md) | Second independent audit before production |
| [B15](stories/B15-production-cluster.md) | Production stage — first promotion and tuning |
| [B16](stories/B16-archive-scale-campaign.md) | Run an archive-scale campaign and measure it |

### Not started — after production

| Id | Story |
|---|---|
| [B17](stories/B17-two-s3-principals.md) | Give jobs and the reconciler separate, minimal S3 credentials |
| [B18](stories/B18-priority-lanes.md) | Let urgent volumes jump the queue |
| [B19](stories/B19-iiif-cache-decision.md) | Decide whether an image cache is needed |
| [B20](stories/B20-quality-prediction-in-batch.md) | Use the quality-prediction step in batch pipelines |

The productionalisation path is deliberately **govern the inputs → DEV via
Argo CD → policy on → promotion pipeline → audit → production**: B11 makes
the campaigns repo a reviewed, protected source of truth (the GitOps side of
B04); B12 puts the platform itself under GitOps with Argo CD on the DEV
cluster; B13 turns on the "only our images run" control there and B14
hardens it; B26 puts dependency updates on a routine; B37 and its per-image
stories (B41–B45, U08) bring every image — the hand-built GPU wrapper
included — under the same CI build, provenance and scan; B36 makes one local
registry the only source of images and B35 puts the models in it as signed
artifacts, closing the last internet egress; B34 adds Kargo so a release
moves dev → staging → prod only by verified promotion; B40, B59 and B60 make
the system observable in Grafana; B38 and the per-diagram stories (B46–B53,
U09) give every part a picture; the per-page stories (B54–B57) add the pages
the new machinery needs and B58 keeps every page honest from then on; B33
then audits the result independently and B15 is the first production
promotion. B16 (the measured archive-scale run) needs B10, B11 and B15. B17
can be done any time; B18/B19 as operations and the B16 numbers demand; B20
when feature #2770 lands.
