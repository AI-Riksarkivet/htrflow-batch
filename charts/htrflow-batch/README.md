# htrflow-batch (Helm chart)

The platform half of htrflow-batch on a Kubernetes cluster: the Kueue queue
objects, the model-cache PVC, the web front (campaign browser, Universal
Viewer and the status API) with the results proxy that serves result files
behind a login, a NetworkPolicy for every pod role, and the Kyverno policies
that hold the namespace to digest-pinned images from allowed repositories,
pinned model revisions and the exact Job shape the converter renders.

**Campaigns are not in this chart.** `htrflow-campaigns render`
(`packages/converter`) turns a campaigns repo into pipeline and campaign
ConfigMaps and Indexed Jobs, and `htrflow-campaigns apply` applies them, by
hand or as an Argo CD hook
([How campaigns run](../../docs/how-it-works/campaigns.md)).

Every value is declared in `values.schema.json` (unknown keys and wrong
types are refused), explained in `values.yaml` and tabulated in
[Chart values](../../docs/reference/chart.md). `values-prod.yaml` is the
production profile. `ci/` holds the render fixtures lint and tests use;
they are placeholders, never an install.

## Prerequisites

- **Kueue**, CRDs and controller. The chart renders ResourceFlavor,
  ClusterQueue, LocalQueue and WorkloadPriorityClass objects and installs
  neither (`make install-kueue` on a dev cluster).
- **Kyverno** when `security.policies.enabled` or
  `security.verifyImages.enabled`. The policies are `kyverno.io/v1`
  ClusterPolicies, which Kyverno deprecates in 1.19 and removes in 1.20:
  stay on the 1.19 line (`make install-kyverno` pins it) until they are
  ported.
- A **session Secret** (`results.sessionSecret`, key `key`: 32 random
  bytes, base64) that seals login sessions. Not needed with
  `web.enabled: false`.
- An **S3 Secret** (`s3.existingSecret`) with `credentials` in AWS ini
  format and `S3_BUCKET`, plus `S3_ENDPOINT` unless real AWS and an optional
  `S3_VERIFY_TLS: "false"`. Nothing here creates it; on a dev cluster
  [`charts/htrflow-devstack`](../htrflow-devstack) does.
- `web.image` **pinned by digest**. A tag is refused unless
  `security.allowTagImages`, which is for an iteration loop only.
- The namespace's **Pod Security labels**. Helm cannot label a namespace it
  did not create, so run `make psa-labels` once; it reads
  `security.psaEnforce`.

## Installing

[Deploy](../../docs/getting-started/deploy.md) has the production install
from `values-prod.yaml` and the hardening steps;
[Dev cluster](../../docs/development/dev-cluster.md) the dev cluster with
`charts/htrflow-devstack`. `web.enabled: false` leaves the web front and the
results proxy out: campaigns still run and publish to the bucket, and
nothing serves a browser.

## Upgrading

Always `helm upgrade --reset-then-reuse-values`, or pass a full values
file: plain `--reuse-values` keeps the old chart's defaults, and the chart
refuses a values tree that lost `network`. Upgrade the chart first, then
move the hook image and `CONVERTER_REF` in every campaigns repo to the same
release: the job-shape policy admits exactly what that release's converter
renders.

A breaking change is refused at render time, in a sentence that names the
value to change. The steps still worth knowing:

- **0.16**: results are read through the results proxy. `results.sessionSecret`
  is required, `resultsUrl` must end in `/results`, and `converter.yaml`'s
  `results_url` is set to the same value in every campaigns repo.
- **0.14**: `publicResultsBase` became `resultsUrl`, same value.
- **0.13**: `network.iiifCidrs` has no default, and `values-prod.yaml`
  empties the network lists and refuses to render until they are set.
- **0.11**: policies off need `security.policies.allowDisabled`. The web
  Service keeps client addresses, so `network.web.ingressCidrs` lists
  browsers, not nodes, and an empty or wider-than-/8 list needs
  `network.web.allowPublicIngress`.

The full notes for every step are in this file's history
(`git log -p -- charts/htrflow-batch/README.md`).

## Adopting hand-applied resources

Helm refuses to manage an object it did not create. On a cluster where the
model-cache PVC was applied by hand, either keep it outside the chart
(`modelCache.create=false`) or adopt it once before enabling the value:

```bash
kubectl -n <namespace> annotate pvc <modelCache.name> \
  meta.helm.sh/release-name=<release> meta.helm.sh/release-namespace=<namespace> --overwrite
kubectl -n <namespace> label pvc <modelCache.name> app.kubernetes.io/managed-by=Helm --overwrite
```

The `nvidia` RuntimeClass and the device-plugin DaemonSet belong to
`charts/htrflow-devstack`, whose README has their recipe.

## Changelog

### 0.19.0 — 2026-10-20

Added:
- **`web.enabled`** (`true`): `false` renders neither the web front nor the
  results proxy, and asks for neither `resultsUrl` nor
  `results.sessionSecret`.
- **`network.iiifPorts`** (`[443, 80]`): the ports campaign pods reach the
  IIIF servers on, where 443 and 80 were hard-wired.
- `queue.resources` refuses a resource named twice and a quota of nothing,
  and `queue.priorityClasses` a class named twice, as `queue.flavors`
  already did.
- `modelCache.name`, `s3.existingSecret` and `queue.name` are held to
  object-name rules by the schema.

Changed:
- **`values-prod.yaml`** sets `security.jobImageRepos` to the wrapper's
  repository: the allow-list admits the web and converter images too.
- The install notes say whose ClusterQueue the queue is on when
  `queue.createClusterQueue` is false.
- The packaged chart no longer carries `ci/`.
- The web Deployment no longer sets `HTRFLOW_INTERNAL_RESULTS_BASE` (its
  default is the results proxy), and the duplicate digest check on
  `web.image` is gone.
- This README is the short version; the per-version notes back to 0.1.0
  are in its history.

### 0.18.0 — 2026-10-15

`web.image` is the v0.12.0 web image. No value or template changes.

### 0.17.0 — 2026-10-05

`web.image` is the v0.11.0 web image, whose wrapper carries htrflow's
QualityPrediction step. No value or template changes.

Older entries: `git log -p -- charts/htrflow-batch/README.md`.
