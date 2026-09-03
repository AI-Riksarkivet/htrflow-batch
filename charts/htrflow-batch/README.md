# htrflow-batch (Helm chart)

Kueue-gated batch HTR platform around the htrflow image: queues, the
model-cache PVC and the web front (campaign browser, Universal Viewer and
the read-only status API in one Deployment).

**Campaigns are Kubernetes Indexed Jobs, not objects this chart renders.**
`packages/converter` (`htrflow-campaigns render <repo-dir> --out <dir>`)
turns a campaigns repo into pipeline/campaign ConfigMaps and Jobs; those are
applied outside this chart (`kubectl apply`, or Argo CD watching a
`rendered/` directory) — see
[docs/how-it-works/campaigns.md](../../docs/how-it-works/campaigns.md). This
chart only renders what those Jobs and the status page need: the Kueue
queue, the model-cache PVC, NetworkPolicies for `app: htrflow-batch` /
`app: htrflow-warmup` pods, and the web front (`htrflow-web`).

Every value is declared in `values.schema.json` (unknown keys and wrong
types are rejected); `values.yaml` documents each one and
[docs/reference/chart.md](../../docs/reference/chart.md) tabulates them.
`ci/full-values.yaml` turns every optional feature on for `helm lint -f` /
`helm template -f` / `kubeconform` (`make helm-template`).

PoC-only in-cluster infrastructure (RustFS S3, a registry, the NVIDIA
device plugin) is a separate chart:
[charts/htrflow-devstack](../htrflow-devstack).

## Prerequisites

- **Kueue CRDs must already be installed on the cluster.** This chart renders
  `ResourceFlavor` / `ClusterQueue` / `LocalQueue` objects
  (`templates/kueue.yaml`) but does not install the Kueue controller or its
  CRDs itself. The ClusterQueue admits LocalQueues from the release namespace
  only.
- Namespace creation is left to Helm (`--create-namespace`); the chart does
  not render a `Namespace` object for its own release namespace, so the Pod
  Security labels are applied once with `make psa-labels` (reads
  `security.psaEnforce`).
- An S3 Secret (`s3.existingSecret`, default `htr-batch-s3`) with a
  `credentials` key in AWS ini format plus `S3_BUCKET` (and `S3_ENDPOINT`
  unless real AWS) — this chart documents the convention but never creates
  it. The batch/warm-up Jobs the converter renders read it; for the PoC,
  `charts/htrflow-devstack`'s RustFS renders it instead (keep `s3.bucket` /
  `s3.existingSecret` here in step with that chart's `s3.bucket` /
  `s3.secretName`).
- `web.image` must be **digest-pinned** (`…@sha256:…`). A tag is refused
  unless `security.allowTagImages=true` (PoC iteration only; tags are then
  pulled on every rollout).

## Installing and replaying the PoC

The install commands — the production-shaped install, the hardening steps,
and the bare-k3s replay with `charts/htrflow-devstack` — live in one place:
[docs/getting-started/deploy.md](../../docs/getting-started/deploy.md), with
the day-to-day loop in
[docs/development/local-k3s.md](../../docs/development/local-k3s.md).
Cluster-local constants come from the repo-root `.env` (`.env.example` has
the PoC defaults). The one image to pin: `make poc-push` builds and pushes
the web image (`packages/web` plus the SPA and Universal Viewer, all in
`.docker/htrflow-web.dockerfile`) and prints the digest for `web.image` —
see [docs/reference/chart.md](../../docs/reference/chart.md).

## Upgrading

Always `helm upgrade --reset-then-reuse-values` (or pass a full values
file). Plain `--reuse-values` keeps the *old* chart's defaults, which once
rendered every NetworkPolicy away; the chart now fails loudly when
`.Values.network` is missing.

Everything below this line is history: each entry names the objects and
value keys **as they were at that version** — `api.*`, `viewer.*`,
`htrflow-api`, `templates/api.yaml`, `htr-api` — not their 0.4.0
successors. Renaming them here would make the upgrade notes wrong for
anyone actually on that version.

### From 0.2.0 to 0.3.0 — what to decide first (B63: campaigns as Indexed Jobs)

| Change | What to do |
|---|---|
| **The old GitOps CronJob controller, its own values block and template are gone.** Campaigns are Indexed Jobs rendered by `packages/converter` and applied outside this chart. | `kubectl -n <namespace> get cronjob` to find the leftover CronJob from the old release and `delete` it once, then switch to `make campaigns-apply DIR=…` (or the Argo CD Application the campaigns repo's CI wires up). |
| **`templates/pipelines.yaml` / `.Values.pipelines` are gone.** Pipeline ConfigMaps and their warm-up Jobs are rendered by the converter alongside campaigns, not by this chart. | Drop `pipelines.*` from your values; convert the pipeline YAML files into a campaigns repo's `pipelines/` directory instead. |
| **`templates/job-example.yaml` / `.Values.exampleJob` are gone.** | Use a real (small) campaign for smoke-testing instead. |
| **`devStack.*` moved to a separate chart, `charts/htrflow-devstack`**, except `devStack.allowTagImages` → `security.allowTagImages` (it gates this chart's own `viewer.image`/`api.image`, not anything devstack-only). **`devStack.gitDaemon` was not carried over** — it fed the old CronJob controller over `git://`, which is also gone in 0.3.0, so it had no consumer left. | `helm uninstall` nothing yet: install `charts/htrflow-devstack` alongside this chart first (`make install-devstack`), *then* upgrade this chart — its NetworkPolicies for RustFS/registry moved with it; anything that hand-clones the old in-cluster git daemon needs a different path now. |
| **New: the read API** (`api.image`, `api.resources`) — a Deployment `htrflow-api`, read-only RBAC on Jobs/Pods/ConfigMaps, `Service htrflow-api:8081`, proxied by the viewer at `/api/`. Always rendered (no `enabled` flag): `publicResultsBase` and `network.apiServer.cidr` are now required at render time (previously only needed when the old CronJob controller was turned on). | Set `api.image` to a digest-pinned `htrflow-api` image; set `network.apiServer.cidr` if the cluster's kube-apiserver endpoint cannot be `lookup`ed (e.g. `helm template`, or a kubeconfig without list-nodes RBAC). |
| **Results are namespaced.** They land at `<namespace>/<pipeline>/<volume>/…` (`S3_PREFIX=<namespace>/`, set by the converter), where 0.2.0 wrote `<pipeline>/<volume>/…`. There is no flag for the old layout. | Move existing data once before upgrading: `aws s3 mv --recursive s3://<bucket>/<pipeline>/ s3://<bucket>/<namespace>/<pipeline>/` for every pipeline id, and `sources/` the same way (`<namespace>/sources/…`). `status/` stays at the bucket root — it is namespace-free by design. |
| **`viewer.statusBase` is gone; `/config.js` sets `window.API_BASE`.** The campaign browser reads campaigns from the read API — there is no status document any more. | Nothing to set — `API_BASE` is always `/api/v1` (same-origin, proxied by nginx). |
| **`job.*` values are gone.** Runtime class, node selector, tolerations, deadlines and byte caps are now the converter's `converter.yaml` (`runtime_class`, `node_selector`, `tolerations`, `max_seconds`, `manifest_max_bytes`, `fetch_max_bytes`), not this chart's. | Move any `job.*` overrides into the campaigns repo's `converter.yaml`. |

### From 0.1.0 to 0.2.0

| Change | What to do |
|---|---|
| **Digest gate.** `viewer.image` (and, through 0.2.0, the old CronJob controller's own image value) must be `@sha256:` pins. | Pin the digests, or `--set security.allowTagImages=true` (named `devStack.allowTagImages` before 0.3.0) for the PoC loop only. |
| **Model-cache PVC is rendered** (`modelCache.create=true`, default). Helm refuses to take over a PVC it did not create. | Either `--set modelCache.create=false`, or adopt the existing PVC once (below) — adoption is the better end state (`resource-policy: keep` protects it). |
| **`image.*` and `s3.endpoint` removed.** | Drop them from your values; the schema rejects unknown keys. Campaign Jobs pin their image in the pipeline YAML; pods read the endpoint from the Secret. |
| **Namespace default deny** (`network.defaultDeny=true`). | Anything hand-applied in the namespace needs its own NetworkPolicy. |
| **RustFS console off by default**; `devStack.rustfs.nodePortConsole` → `devStack.rustfs.console.{enabled,nodePort}` (now `charts/htrflow-devstack`'s `rustfs.console.*`). | `--set rustfs.console.enabled=true` on the devstack chart if still wanted. |
| **`security.psaEnforce`** (default `baseline`, historically because the devstack chart's git daemon ran as root — that daemon is gone in 0.3.0, so nothing left in either chart needs `baseline` by default; the value itself was not changed here, revisit it). | `make psa-labels` after the upgrade; `restricted` is worth trying now. |
| **`queue.resources` default** now admits one Job's worth of resources (cpu 4 / 8Gi / 1 GPU). | Raise it to run more volumes in parallel. |

## Adopting hand-applied resources

Helm refuses to manage an object it did not create. On a cluster where the
model-cache PVC was applied by hand, either keep it outside the chart
(`modelCache.create=false`) or adopt it once before enabling the value:

```bash
kubectl -n htr-batch annotate pvc htr-test-data \
  meta.helm.sh/release-name=htr meta.helm.sh/release-namespace=htr-batch --overwrite
kubectl -n htr-batch label pvc htr-test-data app.kubernetes.io/managed-by=Helm --overwrite
```

(The `nvidia` RuntimeClass, the kube-system device-plugin DaemonSet and the
the git daemon that historically ran alongside RustFS/registry is gone
entirely (no consumer left once the old CronJob controller was removed) — see
`charts/htrflow-devstack`'s README for the RuntimeClass/DaemonSet adoption
recipe.)

## The web front

`htrflow-web` (Deployment, Service `htrflow-web:8081` on NodePort
`web.nodePort`) is the whole browser-facing surface: the campaign browser at
`/`, Universal Viewer at `/uv.html`, and `GET /api/v1/jobs[/{ns}/{name}]`
read-only over the Indexed Jobs a campaign renders to — Role/RoleBinding
scoped to `get`/`list`/`watch` on `jobs`/`pods`/`configmaps` in the release
namespace, never a ClusterRole. It is the one pod in this chart with
`automountServiceAccountToken: true` (everything else has it off) because it
*is* a Kubernetes API client. NetworkPolicy `htr-web` lets browsers in from
`network.web.ingressCidrs` and lets it out to DNS and the apiserver only.
`/config.js`, built into the image, points the campaign browser at
`window.API_BASE = "/api/v1"` — same-origin, no proxy.

## Changelog

Everything below this line is history: each entry names the objects and
value keys **as they were at that version** — `api.*`, `viewer.*`,
`htrflow-api`, `templates/api.yaml`, `htr-api` — not their 0.4.0
successors. Renaming them here would make the upgrade notes wrong for
anyone actually on that version.

### 0.5.0 — 2026-09-03 (B63: an identity for `apply`)

Added:
- `templates/apply-rbac.yaml`, behind **`apply.rbac.enabled` (default
  `false`)**: ServiceAccount/Role/RoleBinding `htrflow-campaigns` for
  `htrflow-campaigns apply` (packages/converter) when it runs *inside* the
  cluster — an Argo CD `PostSync` hook, a CI Job — rather than from an
  operator's kubeconfig. Role, not ClusterRole: `get`/`list`/`create`/
  `patch`/`delete` on `jobs` and `configmaps` (create and patch are the
  server-side apply; delete is `--prune`) and `get`/`list`/`patch` on
  `workloads.kueue.x-k8s.io` (the pause sync's `spec.active`).

Not breaking: leaving `apply.rbac.enabled` at `false` renders nothing new.
The values schema is strict, so a values file that predates 0.5.0 is
accepted unchanged (the key has a default) — but `--reset-then-reuse-values`
still applies as always.

### 0.4.0 — 2026-09-02 (B63: one web front)

Breaking:
- **Removed**: the viewer template — the nginx Deployment, its Service,
  its `config.js`/`default.conf` ConfigMap and its `/api/` proxy — together
  with the whole `viewer` values block (`enabled`, `image`, `nodePort`,
  `defaultManifest`, `securityHeaders.enabled`) and the
  `htr-viewer` NetworkPolicy. `network.viewer` is renamed `network.web`.
- `htrflow-web` takes the NodePort (`web.nodePort`, default 30800, container
  port 8081): its image now carries the campaign browser and Universal
  Viewer as static files and serves the same three security headers nginx
  did. `defaultManifest` has no replacement — bookmark the
  `uv.html#?manifest=…` URL.

| Change | What to do |
|---|---|
| The viewer Deployment, Service and ConfigMap are gone; `htrflow-web` is on the NodePort. | Translate your values file: drop the whole `viewer` block, move its `nodePort` to `web.nodePort` and its `image` to nothing (build one `htrflow-web` image with `make build-web`), rename `network.viewer` to `network.web`. The schema is strict, so an untranslated file is rejected at upgrade time rather than silently ignored. |
| **The upgrade fails with `nodePort: Invalid value: 30800: provided port is already allocated`.** Helm creates the new `htrflow-web` Service before deleting the retired viewer Service, and they want the same NodePort. | Delete the old Service once, then upgrade: `kubectl -n <namespace> delete svc uv4-viewer`. Everything else the viewer left (its Deployment, ConfigMap and NetworkPolicy) is removed by the upgrade itself. |

### 0.3.0 — 2026-09-01 (B63: campaigns as Indexed Jobs)

Breaking:
- **Removed**: the old GitOps CronJob controller (its own template file,
  values block, ServiceAccount/Role/RoleBinding and NetworkPolicy) —
  campaigns are Kubernetes Indexed Jobs rendered by `packages/converter`,
  not run from a schedule.
  `templates/pipelines.yaml` / `.Values.pipelines` — pipeline ConfigMaps and
  warm-up Jobs are rendered by the converter alongside campaigns.
  `templates/job-example.yaml` / `.Values.exampleJob` — the smoke-Job PoC
  aid; use a real campaign instead. `.Values.job` — runtime
  class/nodeSelector/tolerations/deadlines/byte caps are now the
  converter's `converter.yaml`, not this chart's.
- **Moved**: `devStack.{rustfs,registry,nvidiaDevicePlugin}` and
  `templates/devstack-{rustfs,registry,nvidia}.yaml` to a new chart,
  `charts/htrflow-devstack` (own values, own NetworkPolicies for
  RustFS/registry-init). `devStack.allowTagImages` became
  `security.allowTagImages` (it gates this chart's own `viewer.image` /
  `api.image`). `devStack.gitDaemon` / `templates/devstack-gitdaemon.yaml`
  were **removed, not moved**: the old GitOps CronJob controller that
  polled it over `git://` is also gone in 0.3.0, so the daemon had no
  consumer left.
- `values.schema.json`: `s3`, `publicResultsBase`,
  `modelCache`, `queue`, `api`, `security`, `network`, `viewer` are the only
  top-level keys; everything above is rejected as unknown.

Added:
- `api.{image,resources}` renders the read API (`templates/api.yaml`):
  Deployment `htrflow-api`, ServiceAccount + Role + RoleBinding (read-only
  `jobs`/`pods`/`configmaps`, this namespace only), Service
  `htrflow-api:8081`, NetworkPolicy `htr-api` (ingress from the viewer only;
  egress to DNS and the apiserver only — same CIDR lookup the old CronJob
  controller's policy used). Always rendered: `publicResultsBase` and
  `network.apiServer.cidr` are required values now (previously only when
  that controller was turned on).
- Viewer: nginx `location /api/` proxies to `htrflow-api:8081/api/`;
  `/config.js` sets `window.API_BASE = "/api/v1"` — `viewer.statusBase` and
  its runtime counterpart are gone, since the campaign browser now reads the
  read API directly instead of a status document. Its NetworkPolicy gained
  egress to the API (it previously needed none).
- `htr-batch-job`'s S3 egress rule for an in-namespace `app: rustfs` pod is
  now unconditional (a no-op podSelector match unless
  `charts/htrflow-devstack`'s RustFS is installed) — the two charts share
  no Helm values to gate it on.

### 0.2.0 — 2026-08-26 (audit remediation, work package A3)

Breaking:
- `image.*` and `s3.endpoint` removed (dead values; the pods take the
  endpoint from the Secret, campaign Jobs pin their image in the pipeline).
- The old CronJob controller's own image value / `viewer.image` must be
  digest-pinned unless `devStack.allowTagImages=true`. Tag refs get
  `imagePullPolicy: Always`.
- `devStack.rustfs.nodePortConsole` → `devStack.rustfs.console.{enabled,nodePort}`;
  the console is off by default. RustFS credentials are no longer
  `rustfsadmin` — see above.
- `queue.resources` defaults now admit the Job the old CronJob controller
  built (cpu 4 / memory 8Gi / nvidia.com/gpu 1).
- `values.schema.json`: unknown keys are rejected; `.Values.network` is required.
- Namespace-wide default-deny NetworkPolicy (`network.defaultDeny`, on):
  hand-applied pods in the namespace need their own policy.

Added:
- `modelCache.{create,name,size,storageClass,accessModes}` renders the model
  cache PVC (kept on uninstall) and feeds `RECONCILER_DATA_PVC`.
- The (now-removed) CronJob controller: `startingDeadlineSeconds: 120`,
  `activeDeadlineSeconds` from its own tick-deadline value (600), Lease RBAC
  (`coordination.k8s.io/leases`), env `RECONCILER_TICK_SECONDS`,
  `RECONCILER_TICK_DEADLINE_SECONDS`, `RECONCILER_GIT_TIMEOUT`, optional
  `GIT_TOKEN` (its own git-token-secret value),
  `RECONCILER_MAX_VALIDATIONS_PER_TICK`, `RECONCILER_FETCH_MAX_BYTES`,
  `RECONCILER_LEASE_NAME`, `RECONCILER_JOB_{MIN_DEADLINE_SECONDS,SECONDS_PER_PAGE,RUNTIME_CLASS,NODE_SELECTOR,TOLERATIONS}`,
  `RECONCILER_JOB_{MANIFEST_MAX_BYTES,FETCH_MAX_BYTES}`,
  `RECONCILER_ALLOWED_IMAGE_REPOS`, `RECONCILER_REQUIRE_MODEL_REVISION`.
- `job.{runtimeClassName,nodeSelector,tolerations,minDeadlineSeconds,secondsPerPage,manifestMaxBytes,fetchMaxBytes}`;
  the example Job mirrors them plus `backoffLimit: 0` + `podFailurePolicy`.
- `security.{allowedImageRepos,requireModelRevision,psaEnforce,verifyImages.*}`;
  optional Kyverno `ClusterPolicy` (cosign keyless) when `verifyImages.enabled`.
- Viewer: restricted securityContext (uid 101, read-only rootfs, no SA
  token), nginx security headers (`viewer.securityHeaders.enabled`),
  `/config.js` served from the ConfigMap (`viewer.statusBase`, defaulting to
  `publicResultsBase`) so the SPA finds its status document under its CSP,
  pod rolls on config change.
- devStack: RustFS/registry restricted securityContext (RustFS uid 10001,
  registry `devStack.registry.runAsUser`), digest-pinned images, bucket-init
  hook Job, `devStack.gitDaemon.*` (seeded from the bucket, own NetworkPolicy),
  `helm.sh/resource-policy: keep` on the S3 Secret, PVCs and the registry
  Namespace, sizing notes.
- ClusterQueue `namespaceSelector` limited to the release namespace;
  its own `egressCidrs` value narrowed to GitHub's git ranges;
  `network.viewer.ingressCidrs`.

### 0.1.0

Initial chart (queues, pipelines, viewer, devStack, the CronJob controller, NetworkPolicies).
