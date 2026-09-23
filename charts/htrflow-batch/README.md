# htrflow-batch (Helm chart)

Kueue-gated batch HTR platform around the htrflow image: queues, the
model-cache PVC and the web front (campaign browser, Universal Viewer and
the read-only status API in one Deployment).

**Campaigns are Kubernetes Indexed Jobs, not objects this chart renders.**
`packages/converter` (`htrflow-campaigns render <repo-dir> --out <dir>`)
turns a campaigns repo into pipeline/campaign ConfigMaps and Jobs; those are
applied outside this chart by `htrflow-campaigns apply` alone — by hand, or
as the Argo CD hook of a campaigns repo — see
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
  `ResourceFlavor` / `ClusterQueue` / `LocalQueue` / `WorkloadPriorityClass`
  objects (`templates/kueue.yaml`) but does not install the Kueue controller
  or its CRDs itself. The ClusterQueue admits LocalQueues from the release
  namespace only.
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

## Installing

The production-shaped install and the hardening steps live in
[docs/getting-started/deploy.md](../../docs/getting-started/deploy.md); a dev
cluster with `charts/htrflow-devstack` is in
[docs/getting-started/try-it.md](../../docs/getting-started/try-it.md), and
the contributor loop in
[docs/development/dev-cluster.md](../../docs/development/dev-cluster.md).
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
value keys **as they were at that version**. `/config.js` is one of them —
since the 2026-09-14 web audit the read API serves it from its own
environment, so an entry below describing it as a file in an image, or as a
ConfigMap the nginx viewer mounted, is describing the version it names — `api.*`, `viewer.*`,
`htrflow-api`, `templates/api.yaml`, `htr-api` — not the `web.*` /
`htrflow-web` / `templates/web.yaml` they became in 0.4.0. Renaming them
here would make the upgrade notes wrong for anyone actually on that
version.

### From 0.12.0 to 0.13.0 — what can stop an upgrade

The chart and the converter must come from the same release from here on:
the `job-shape` policy compares a campaign or warm-up Job's scripts with the
ones the converter renders, character for character.

| Change | What to do |
|---|---|
| **`values-prod.yaml` empties `network.s3Cidrs`, `network.clusterCidrs` and `network.iiifCidrs` and refuses to render without them**, as its header always said it did; it also sets **`network.s3InNamespace: false`**, so no pod labelled `app: rustfs` is a route. An empty `network.clusterCidrs` or `network.iiifCidrs` is refused on any values file, and so is an empty `network.s3Cidrs` with `s3InNamespace: false`. | Pass your S3 endpoint's, your cluster's pod and service, and your IIIF origins' ranges with `--set`, as the deploy page's install command does. |
| **`job-shape`, a new policy with `security.policies.enabled`.** A campaign Job may read only `s3.existingSecret`, a warm-up Job only `hfToken.existingSecret` (new, default empty), both only the `modelCache.name` PVC, with no ServiceAccount token and the converter's commands. | If `converter.yaml` sets `hf_token_secret`, set `hfToken.existingSecret` to the same name, or the warm-up is refused. Keep `s3_secret` and `data_pvc` equal to `s3.existingSecret` and `modelCache.name`, as before. |
| **`rbac-scope` holds the apply identity's Workload patches** to `spec.active`, on the Workload of a converter-labelled Job. | Nothing, unless something else uses the `htrflow-campaigns` ServiceAccount on Workloads. |
| **The apply Role gains the coordination Lease** `htrflow-campaigns-apply` (`create` on leases, `get`/`update` on that one), which `htrflow-campaigns apply` holds for its whole run. | Nothing: without it an in-cluster apply of this release fails closed. |
| **`security.verifyImages` reads Sigstore bundles** (`type: SigstoreBundle`), the form cosign 3 signs in. The 0.12.0 policy looked for `.sig` tags the release no longer writes and refused every published image. | Nothing: an install that had turned verification on starts admitting the signed release. |
| **The model cache is one directory per pipeline recipe** (converter): every campaign Job's pod template mounts it by `subPath`. A campaign Job the v0.5.0 converter rendered and that is still live cannot take the new pod template (a Job's template is immutable), so `apply` leaves it as it was (exit 3) until it finishes; pausing still works through its Workload. Warm-ups download into the new directories. | Let running campaigns finish. Once no v0.5.0 Job is live, the old `/data/hf` and `/data/warmup` on the PVC are orphaned and can be deleted. Size `modelCache.size` for a copy of each recipe's models. |
| **Wide egress ranges lose the internal ranges inside them**, not only a literal `0.0.0.0/0`: `s3Cidrs: [0.0.0.0/0]`, `iiifCidrs` split into halves and `apply.gitCidrs` now carve out the cluster, node, link-local, loopback and private ranges. `network.privateCidrs` adds `100.64.0.0/10`. | Name a private host you need in `iiifCidrs` / `s3Cidrs` by its own range, which stays reachable, or narrow `network.privateCidrs`. |

### From 0.11.0 to 0.12.0 — nothing stops an upgrade

Every new key is optional and off by default: an install that sets none of
them renders the same objects as 0.11.0. Upgrade with
`--reset-then-reuse-values` as always.

### From 0.10.0 to 0.11.0 — what can stop an upgrade

| Change | What to do |
|---|---|
| **Policies off must be accepted explicitly.** With `security.policies.enabled: false` the chart refuses to render unless **`security.policies.allowDisabled: true`** says the namespace is meant to run without admission policy. | Production: install Kyverno and set `security.policies.enabled=true` (values-prod.yaml does). A dev cluster without Kyverno: `--set security.policies.allowDisabled=true`. |
| **The web Service keeps client addresses** (`externalTrafficPolicy: Local`). `network.web.ingressCidrs` now matches the browser's own address, not the node's. | Replace the node range in `network.web.ingressCidrs` with the clients' ranges; keep it only if browsers run on the nodes. |
| **An empty or wider-than-/8 `network.web.ingressCidrs` is refused** unless `network.web.allowPublicIngress: true`. An empty list used to admit every address. | List the clients' ranges, or set the flag if any address really may reach it. |

### From 0.7.0 to 0.8.0 — the one thing that stops an upgrade

| Change | What to do |
|---|---|
| **A `0.0.0.0/0` web ingress must be accepted explicitly.** `helm upgrade` fails with a sentence naming `network.web.allowPublicIngress` unless the release either lists real ranges in `network.web.ingressCidrs` or sets that flag. | Decide which it is. A dev cluster reached from wherever a browser happens to be: `--set network.web.allowPublicIngress=true`. Anything else: `--set network.web.ingressCidrs='{<range>}'` (include the node range — NodePort traffic arrives SNAT'd from the node). *From 0.11.0 on, the Service keeps client addresses (`externalTrafficPolicy: Local`): list the clients' ranges, not the node range — see Web front ingress on the Deploy page.* |
| **New enforcing policies with `security.policies.enabled`.** The apply identity may delete only converter-labelled objects, and the web front may write only `campaign-<name>-status` ConfigMaps. | Nothing, unless something else in the namespace uses those ServiceAccounts. The policies need Kyverno; with `policies.enabled: false` they are not rendered. |
| **Catch-all egress loses link-local and the private ranges.** | If a batch Job or warm-up legitimately reaches a private address, name that range in `network.iiifCidrs` / `network.s3Cidrs` — a named range is its own rule and stays reachable — or narrow `network.privateCidrs`. |
| **S3 egress by CIDR is limited to `network.s3Ports` (default 443).** | Set `network.s3Ports` if your endpoint answers on another port. |

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

(The `nvidia` RuntimeClass and the kube-system device-plugin DaemonSet moved
to `charts/htrflow-devstack` in 0.3.0 — see that chart's README for their
adoption recipe.)

## The web front

`htrflow-web` (Deployment, Service `htrflow-web:8081` on NodePort
`web.nodePort`) is the whole browser-facing surface: the campaign browser at
`/`, Universal Viewer at `/uv.html`, and `GET /api/v1/jobs[/{ns}/{name}]`
read-only over the Indexed Jobs a campaign renders to — Role/RoleBinding
scoped to `get`/`list` on `jobs`/`pods`/`configmaps` (plus `create`/`patch`
on `configmaps`, for the per-campaign status record it writes) in the release
namespace, never a ClusterRole. It is the one pod in this chart with
`automountServiceAccountToken: true` (everything else has it off) because it
*is* a Kubernetes API client. NetworkPolicy `htr-web` lets browsers in from
`network.web.ingressCidrs` and lets it out to DNS, the apiserver and the
results bucket. The pod also **serves `/config.js` itself**, written from its
own environment: `window.API_BASE = "/api/v1"` (same-origin, no proxy) and
`window.RESULTS_BASE` from `publicResultsBase`. There is nothing for an
operator to overwrite — set `publicResultsBase` and the campaign browser
follows.

## Changelog

Everything below this line is history: each entry names the objects and
value keys **as they were at that version** — `api.*`, `viewer.*`,
`htrflow-api`, `templates/api.yaml`, `htr-api` — not the `web.*` /
`htrflow-web` / `templates/web.yaml` they became in 0.4.0. Renaming them
here would make the upgrade notes wrong for anyone actually on that
version.

### 0.13.0 — unreleased (deployment audit fixes)

**Breaking at render time for the production profile, on purpose** — see
*From 0.12.0 to 0.13.0* above.

Added:
- **`job-shape`** ClusterPolicy (with `security.policies.enabled`): a
  campaign or warm-up Job is the shape the converter renders — its
  ServiceAccount and token, Secrets, volumes, containers, scripts, pod
  labels and pipeline source.
- **`hfToken.existingSecret`**: the one Secret a warm-up may read; must equal
  `converter.yaml`'s `hf_token_secret`.
- **`security.jobImageRepos`**: narrows campaign and warm-up Jobs to the
  wrapper's repositories.
- **`network.s3InNamespace`** (default `true`): the in-namespace RustFS
  route; `false` drops it and needs `network.s3Cidrs`.
- **`queue.createFlavor`**, **`queue.createClusterQueue`**,
  **`queue.clusterQueueName`**, **`queue.createPriorityClasses`**: each
  cluster-scoped Kueue object is created or only referenced by name, so a
  second release or an existing Kueue setup installs.
- The apply Role's coordination Lease rules.

Changed:
- **`verify-images`** reads Sigstore bundles (`type: SigstoreBundle`).
- **`model-revision`**: TrOCR, WordLevelTrOCR, Donut and DiT also need
  `model_settings.processor_kwargs.revision`; a pipeline in `binaryData` is
  refused.
- **`rbac-scope`**: the apply identity may change only `spec.active` on the
  Workload of a converter-labelled Job.
- Every egress range carves out the internal ranges inside it;
  `network.privateCidrs` adds `100.64.0.0/10`.
- **`values-prod.yaml`**: `allowedImageRepos` names the three published
  repositories instead of the organisation; the network lists are emptied
  and required; `network.s3InNamespace: false`.

### 0.12.0 — 2026-09-22 (v0.5.0)

Added:
- **`web.service.type`** (`NodePort`, the default, or `ClusterIP`) and
  **`web.ingress.*`** (`enabled`, `className`, `host`, `tlsSecretName`,
  `annotations`): the web front behind an ingress controller. In that mode
  **`network.web.ingressFrom`** admits the controller's pods by selector
  (never an address range, never an empty selector) instead of
  `network.web.ingressCidrs`, and the chart's address guards do not apply:
  the controller's own allow-list is the gate.
- **`apply.gitCidrs`** / **`apply.gitPorts`** (default: none, and at least
  one port when set): egress from the apply identity's pods to the git host,
  for the Argo CD hook that clones the campaigns repo
  (`htrflow-campaigns init` writes it as `argocd/apply.yaml`).

Changed:
- **`web.image`** pins the `v0.5.0` web image
  (`docker.io/riksarkivet/htrflow-web@sha256:1fbabef5…`); campaign
  pipelines pin the wrapper at
  `docker.io/riksarkivet/htrflow-batch@sha256:637fbe4a…`, whose amd64
  variant now runs the driver (htrflow built from the repo's own locked
  base, not an older upstream image). The hook runs
  `docker.io/riksarkivet/htrflow-campaigns@sha256:8c060603…`.
  **`appVersion`** is `0.5.0`.

### 0.11.0 — 2026-09-21 (v0.4.0)

**Breaking at render time, on purpose** — see *From 0.10.0 to 0.11.0*
above.

Added:
- **`security.policies.allowDisabled`** (default `false`). Policies off now
  fails the render with a sentence naming the flag, instead of silently
  running a namespace where any registry, any tag and any unpinned model
  is admitted.
- **`network.apiServer.cidrs`** — every further API server of an HA
  control plane, one /32 each, added to `cidr`. When both are empty the
  chart looks up every address of the `kubernetes` Endpoints, not only the
  first; `helm template` without cluster access still needs one of them.

Changed:
- **The web Service is `externalTrafficPolicy: Local`**, so
  `network.web.ingressCidrs` is matched against the client's address.
- **`network.web.ingressCidrs`**: an empty list, and any range wider than
  /8, now need `network.web.allowPublicIngress: true`.
- **`model-revision`** has a second rule: a step that loads a model may
  carry only `model`, `model_settings` and `generation_settings` under
  `settings`, since htrflow merges any other key over `model_settings`
  and could unpin the model.
- **`images-allowed` / `images-pinned`** also check image volumes, not only
  containers.
- **`verifyImages.imageReferences`** defaults to `"*"`; it narrows to
  `allowedImageRepos` only while `policies.enabled` renders the allow-list
  that refuses everything else.
- **`rbac-scope`**: the apply identity's creates and updates of Jobs and
  ConfigMaps must carry `managed-by: converter`, and on an update so must
  the object replaced — it can no longer label a foreign object to delete
  it.
- **`web.image`** pins the `v0.4.0` web image
  (`docker.io/riksarkivet/htrflow-web@sha256:d86f7466…`); campaign pipelines pin
  the wrapper at `docker.io/riksarkivet/htrflow-batch@sha256:982d1651…`.
  **`appVersion`** is `0.4.0`.

### 0.10.0 — 2026-09-17 (v0.3.0, the first GitHub release)

Changed:
- **`web.image`** now pins the published `v0.3.0` web image
  (`docker.io/riksarkivet/htrflow-web@sha256:3833537f…`), a multi-architecture
  manifest list, signed and attested like the wrapper. The web repository on
  Docker Hub is public from this release; the `v0.2.0` digest the chart pinned
  before could not be pulled anonymously.
- **`appVersion`** is `0.3.0`, the version of the wrapper, web and converter
  released together. Campaign pipelines pin the wrapper at
  `docker.io/riksarkivet/htrflow-batch@sha256:a3a03fa9…`.

No template or value key changed; an upgrade from 0.9.0 only moves the web
Deployment to the new image.

### 0.9.0 — 2026-09-16 (priority classes)

Added:
- **`queue.priorityClasses`** — one cluster-scoped `WorkloadPriorityClass`
  per entry (`templates/kueue.yaml`), on the same API version as the queue
  objects. Default three: `htr-interactive` (1000), `htr-bulk` (0, the
  same rank as a campaign that leaves `priority:` out) and `htr-idle`
  (-10). A campaign's `priority:` now names one of these instead of a
  class that never existed. Kueue orders the queue by class value first,
  then by creation time; preemption stays off (`withinClusterQueue:
  Never`), so a higher class goes ahead of waiting campaigns but never
  evicts a running one. Kueue does not refuse a Job naming a class that
  does not exist (no Workload, no event, "Queued" for ever), so
  `converter.yaml`'s `priority_classes` mirrors this list and
  `htrflow-campaigns validate` refuses a name outside it. An empty list
  renders no class.

### 0.8.0 — 2026-09-14 (deployment audit)

**Breaking at render time, on purpose.** `network.web.ingressCidrs` still
defaults to `["0.0.0.0/0"]`, but the chart now refuses to render that
default unless **`network.web.allowPublicIngress: true`** says the exposure
is deliberate: the web front is a NodePort with no authentication of its
own. An upgrade that says nothing about ingress fails with that sentence.
Either list the ranges that may reach it, or set the flag.

Added:
- **`values-prod.yaml`** — the profile
  [docs/getting-started/deploy.md](../../docs/getting-started/deploy.md)
  starts from: `security.policies.enabled`, the allow-list set to what the
  release publishes, `requireModelRevision`, `verifyImages` and
  `psaEnforce: restricted`. Only the switches; every site-specific value
  stays a `--set`, and a render of the file alone fails asking for them.
- `templates/policies/rbac-scope.yaml` (with `security.policies.enabled`):
  `htrflow-batch-rbac-scope-<ns>`, `Enforce`, **`background: false`** (it
  matches on the requesting ServiceAccount, which a background scan has
  none of). Two rules for the two grants RBAC cannot narrow — a verb covers
  a resource type and never one object's name:
  - `web-writes-status-only` — the web ServiceAccount may CREATE or UPDATE
    only ConfigMaps named `campaign-<name>-status`. Its Role's
    `create`/`patch` otherwise covers `htr-pipeline-<id>`, the pipeline a
    campaign Job mounts.
  - `apply-deletes-only-what-it-rendered` — rendered with
    `apply.rbac.enabled`: the apply ServiceAccount may DELETE only objects
    labelled `htrflow.riksarkivet.se/managed-by: converter`.
- `htr-campaigns-apply` NetworkPolicy (`apply.rbac.enabled` +
  `network.enabled`): DNS and the API server for a pod labelled
  `app: htrflow-campaigns`. Under the default deny that pod previously had
  an identity and no route to the API server, and the apply hung.
- **`network.privateCidrs`** (default the three private blocks) and
  **`network.s3Ports`** (default `[443]`).

Changed:
- Every catch-all (`0.0.0.0/0`) egress now excludes link-local
  (`169.254.0.0/16`), loopback and `network.privateCidrs` as well as the
  cluster and node ranges. A range named in `iiifCidrs`/`s3Cidrs` is its own
  rule and stays reachable.
- The CIDR half of the S3 egress rule names ports (`network.s3Ports`)
  instead of allowing every port of that range.
- `htrflow-batch-model-revision-<ns>` matches **any** ConfigMap carrying a
  `pipeline.yaml` key, not only those labelled `managed-by: converter`.
- The two image policies' **Pod** rules walk `ephemeralContainers` as well
  as `containers` and `initContainers`.
- The `security.verifyImages.subject` examples in `values.yaml` and
  `ci/full-values.yaml` name the organisation that actually signs, and a
  branch ref — publishing is a manual dispatch and never carries a tag ref.

### 0.6.0 — 2026-09-04 (B63: policy is Kyverno's, not the converter's)

Added:
- `templates/policies/`, behind **`security.policies.enabled` (default
  `false`)**: three Kyverno `ClusterPolicy` objects, all
  `validationFailureAction: Enforce`, `background: true`, named with the
  release namespace as a suffix.
  - `htrflow-batch-images-pinned-<ns>` — every container and initContainer
    image of every `Job` and `Pod` in the namespace is `@sha256:`-pinned.
  - `htrflow-batch-images-allowed-<ns>` — rendered only when
    `security.allowedImageRepos` is non-empty: the repository (the part
    before `@`) is one of those prefixes, matched on a path boundary.
  - `htrflow-batch-model-revision-<ns>` — rendered only when
    `security.requireModelRevision`: every `model_settings.model` in a
    converter-rendered pipeline ConfigMap's `pipeline.yaml` carries a 40-hex
    `revision:`.
- `templates/kyverno.yaml` (the `verifyImages` policy) moved unchanged to
  `templates/policies/verify-images.yaml`; it stays behind its own
  `security.verifyImages.enabled`.

Changed, not breaking: `security.allowedImageRepos` and
`security.requireModelRevision` stop being documentation. They were the
campaigns repo's `converter.yaml` keys `allowed_image_repos` /
`require_model_revision`, which are **removed from the converter** in the
same change — a `converter.yaml` still carrying either is now a validation
error pointing here. Leaving `security.policies.enabled` at `false` renders
nothing new, and then nothing enforces the allow-list or the revision rule
at all; the digest *shape* of a pipeline's `image:` is still checked by
`htrflow-campaigns validate`.

Requires Kyverno in the cluster (`make install-kyverno` on the PoC) when
`security.policies.enabled` is `true`. A campaigns repo's CI runs the same
policies with the Kyverno CLI against `rendered/`.

### 0.5.0 — 2026-09-03 (B63: an identity for `apply`)

Added:
- `templates/apply-rbac.yaml`, behind **`apply.rbac.enabled` (default
  `false`)**: ServiceAccount/Role/RoleBinding `htrflow-campaigns` for
  `htrflow-campaigns apply` (packages/converter) when it runs *inside* the
  cluster — an Argo CD `PostSync` hook, a CI Job — rather than from an
  operator's kubeconfig. Role, not ClusterRole: `list`/`create`/`patch`/
  `delete` on `jobs` and `configmaps` (create and patch are the
  server-side apply; delete is `--prune`) and `list`/`patch` on
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
