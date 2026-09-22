# Chart Values

`charts/htrflow-batch` — one chart for the Kueue queue objects, the
model-cache PVC, the web front (campaign browser, Universal Viewer and the
read-only status API in one Deployment), the NetworkPolicies and the Kyverno
policies. Its version is in `Chart.yaml`. The development support stack
(RustFS, an in-cluster registry, the NVIDIA device plugin) is the separate
`charts/htrflow-devstack` chart. Campaigns themselves are not rendered by
either chart: they are Indexed Jobs rendered by `packages/converter` from a
campaigns repo and applied by `htrflow-campaigns apply` (by hand, or as an
Argo CD hook).
Source: [`charts/htrflow-batch/values.yaml`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/charts/htrflow-batch/values.yaml);
every key is declared in `values.schema.json` (unknown keys and wrong types
are rejected at install time). This page is the prose; the generated table of
every key, its default, what it must agree with elsewhere and what it exposes
is [Configuration](configuration.md).

!!! warning "Upgrading: `--reset-then-reuse-values`"

    Always `helm upgrade … --reset-then-reuse-values` (or pass a full values
    file). Plain `--reuse-values` keeps the *old* chart's defaults, which can
    render every NetworkPolicy away; the chart fails loudly when
    `.Values.network` is missing. Values removed in earlier releases, and how
    to translate a values file that still carries them, are in the chart
    README's
    [Upgrading](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/charts/htrflow-batch/README.md#upgrading)
    and Changelog sections.

## Core

| Key | Default | Description |
|-----|---------|-------------|
| `s3.existingSecret` | `htr-batch-s3` | Secret in the release namespace; no template in this chart creates it. Pods read the key **`credentials`** (AWS ini: `[default] aws_access_key_id / aws_secret_access_key`) as a file mounted at `/secrets/s3/credentials` via `AWS_SHARED_CREDENTIALS_FILE`, plus the non-secret `S3_BUCKET` and optional `S3_ENDPOINT` as env. **Nothing is injected with `envFrom`**; only tooling (compose, the devstack's bucket-setup Job, the RustFS server) reads `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` keys directly. Must match `converter.yaml`'s `s3_secret`. See [Security](../how-it-works/security.md) |
| `s3.bucket` | `htr-results` | Results bucket name. No template in this chart reads it — pods take the bucket from the Secret's `S3_BUCKET` — so keep it equal to that key, and to the devstack chart's own `s3.bucket` when the devstack creates the bucket |
| `publicResultsBase` | `""` | **Required** — browser-reachable URL base for published results (viewer manifests and the read API's `resultsBase` embed it). Must match `converter.yaml`'s `public_results_base` |

## Model cache (`modelCache.*`)

Renders the PVC that a pipeline's warm-up Job writes and campaign Jobs mount
read-only — both rendered by the converter, referencing this PVC by
`converter.yaml`'s `data_pvc`. Kept on uninstall
(`helm.sh/resource-policy: keep`). The cache is never evicted: a pipeline's
models stay until the PVC is dropped.

| Key | Default | Description |
|-----|---------|-------------|
| `modelCache.create` | `true` | `false` = a PVC named `modelCache.name` already exists (hand-made, or adopt it — see the chart README) |
| `modelCache.name` | `htr-test-data` | PVC name; must match `converter.yaml`'s `data_pvc` |
| `modelCache.size` | `30Gi` | |
| `modelCache.storageClass` | `""` | `""` = the cluster's default StorageClass |
| `modelCache.accessModes` | `[ReadWriteOnce]` | RWO pins every pod to the node holding the volume — fine on one GPU node, a scheduling constraint beyond it; use an RWX class (or a per-node cache) to scale out |

## Queue (`queue.*`)

| Key | Default | Description |
|-----|---------|-------------|
| `queue.name` | `htr-batch` | LocalQueue name; ClusterQueue is `<name>-cq`, admitting LocalQueues from the release namespace only. Must match `converter.yaml`'s `queue` |
| `queue.flavor` | `default-flavor` | ResourceFlavor |
| `queue.priorityClasses` | `htr-interactive` 1000, `htr-bulk` 0, `htr-idle` -10 | One cluster-scoped `WorkloadPriorityClass` per entry (`name`, integer `value`, `description`); these are the names a campaign's `priority:` may use, and an empty list renders none. Kueue orders the queue by value first (higher first), then by creation time; preemption stays off, so a higher class goes ahead of waiting campaigns but never evicts a running one. A Job with no label ranks at 0, which is why `htr-bulk` is 0: leaving `priority:` out is `htr-bulk`. Kueue does not refuse a Job naming a class that does not exist (no Workload, no event, "Queued" for ever), so `converter.yaml`'s `priority_classes` mirrors these names and `validate` refuses a `priority:` outside them |
| `queue.resources` | cpu 4 / memory 8Gi / nvidia.com/gpu 1 | Covered quotas — every resource an index's pod requests must be listed, or Kueue marks it inadmissible. The default admits exactly one campaign index as the converter renders it (requests cpu 4 / 8 Gi / 1 GPU); raise it to run more volumes in parallel. Indexes stuck `queued` with an idle GPU usually mean a dead Kueue controller, not a busy GPU |

## Web front (`web.*`)

Renders a ServiceAccount, a Role and RoleBinding, and the `htrflow-web`
Deployment + Service (NodePort). The Role is this namespace only, never
cluster-wide: `get`/`list`/`watch` on `jobs`, `pods` and `configmaps`, and on
`configmaps` also **`create` and `patch`**. Those two are one privilege, not
two — a server-side apply of an object that does not exist yet is a create —
and they exist for the one write this service makes: the campaign's
`campaign-<name>-status` ConfigMap
([The record a campaign leaves](../how-it-works/campaigns.md#the-record-a-campaign-leaves)).
RBAC's `resourceNames` cannot express a name *pattern*, and does not apply to
`create` at all, so the grant covers every ConfigMap in the namespace; what
keeps the service to the one object is the service, and a test that greps its
source for any other write. Nothing here may delete anything, or write a Job
or a Pod.

One container serves the campaign browser at `/`, Universal Viewer at
`/uv.html` and the read API at `/api/v1/…`; it is the one pod in this chart
that keeps its ServiceAccount token, because it is the Kubernetes API client
the browser reads through, computing every answer live from Jobs, Pods and
ConfigMaps. It also reads the results bucket directly, for one thing the
Kubernetes API cannot answer: a running volume's `progress.json`
(`ProgressReader`, see [Events and signals](../how-it-works/signals.md)) — so
this pod must reach the results bucket, not only the API server, and its
NetworkPolicy carries an S3 egress rule (the same shape as a batch Job's) for
exactly that. Always rendered — there is no `enabled` flag.

| Key | Default | Description |
|-----|---------|-------------|
| `web.image` | `docker.io/riksarkivet/htrflow-web@sha256:…` | **Must be digest-pinned** unless `security.allowTagImages`. The default is a published web image — the digest of its multi-architecture manifest list, so it resolves on a node of either kind; a single architecture's digest does not. To run your own build, set the digest you pushed (see [Releasing](../development/releasing.md)) |
| `web.nodePort` | `30800` | NodePort; the container listens on 8081. Answered only on the node running the pod (`externalTrafficPolicy: Local`). Unused with `web.service.type: ClusterIP` |
| `web.service.type` | `NodePort` | `NodePort` is the browser's direct way in. `ClusterIP` is for an ingress controller in front (`web.ingress.*`): no node port and no `externalTrafficPolicy`, since the pod then sees only the controller's address |
| `web.ingress.enabled` | `false` | Renders an Ingress for the web front. Refused unless `web.service.type` is `ClusterIP` and `web.ingress.host` and `network.web.ingressFrom` are set. The Ingress carries no authentication, and the `network.web.ingressCidrs` guards do not apply in this mode: set the controller's allow-list (see [Deploy → Behind an ingress controller](../getting-started/deploy.md#behind-an-ingress-controller)) |
| `web.ingress.host` | `""` | The hostname the Ingress routes; required with `web.ingress.enabled` |
| `web.ingress.className` | `""` | `ingressClassName`; `""` = the cluster's default IngressClass |
| `web.ingress.tlsSecretName` | `""` | TLS Secret for `web.ingress.host`, terminated at the Ingress; `""` = no `tls` block |
| `web.ingress.annotations` | `{}` | Annotations on the Ingress, e.g. the controller's source-range allow-list (`nginx.ingress.kubernetes.io/whitelist-source-range`) |
| `web.resources` | requests cpu 50m / 128Mi, limits cpu 500m / 256Mi | |
| `web.internalResultsBase` | `""` | Where THIS POD reaches the results bucket, for `ProgressReader` — `""` (default) means the same address as `publicResultsBase`, correct whenever that URL also resolves to the bucket from inside the cluster (a public S3 endpoint). Set it whenever it does not: a browser-facing address reached through a tunnel or port-forward resolves, from inside the pod, to the pod itself, and the API then silently reads no progress at all. Use the in-cluster address instead, e.g. `http://rustfs.<namespace>.svc.cluster.local:9000/<bucket>` for the devstack's store (see [Dev cluster](../development/dev-cluster.md)) |

The app sends `X-Content-Type-Options: nosniff`, `Referrer-Policy:
strict-origin-when-cross-origin` and `Content-Security-Policy:
frame-ancestors 'none'` on every response (script/style/connect sources stay
governed by the build's own CSP meta). `/config.js`, built into the image,
sets `window.API_BASE = "/api/v1"` — same-origin, because the same process
serves both (see [Campaign Browser](frontend.md)).

## Apply identity (`apply.*`)

| Key | Default | Description |
|-----|---------|-------------|
| `apply.rbac.enabled` | `false` | Renders the ServiceAccount `htrflow-campaigns`, a Role and RoleBinding for `htrflow-campaigns apply` run *inside* the cluster (an Argo CD `PostSync` hook, a CI Job): `list`/`create`/`patch`/`delete` on `jobs` and `configmaps`, `list`/`patch` on `workloads.kueue.x-k8s.io`, release namespace only. Off by default: run from an operator's kubeconfig, the command needs no in-cluster identity, and an idle ServiceAccount that may delete Jobs is a liability. See [Campaign & Pipeline YAML → Pausing](campaign-yaml.md#pausing) |
| `apply.gitCidrs` | `[]` | The git host the Argo CD hook clones the campaigns repo from, by address (a NetworkPolicy cannot name a host): an egress rule for the `app=htrflow-campaigns` pod. Empty = no git egress; the pod reaches only DNS and the API server, which is all `apply` on a local checkout needs. See [Campaign & Pipeline YAML → With Argo CD](campaign-yaml.md#with-argo-cd) |
| `apply.gitPorts` | `[443]` | Ports of that egress rule. At least one: a rule with no ports would open every port |

## Trust boundary (`security.*`)

| Key | Default | Description |
|-----|---------|-------------|
| `security.allowedImageRepos` | `[]` | Repository prefixes a Job or Pod in the namespace may pin, matched on a path boundary before `@sha256:`. Enforced by the `htrflow-batch-images-allowed-<ns>` ClusterPolicy when `security.policies.enabled`; empty = the policy is not rendered. Also the default for `verifyImages.imageReferences` while the policies are enabled. The rule is namespace-wide on purpose, not scoped to this chart's own labels — any supporting workload sharing the namespace (`charts/htrflow-devstack`'s RustFS and its `rustfs-init` hook Job included) needs its image's prefix on this list too, or a label-scoped rule would be one any Job author could opt out of |
| `security.requireModelRevision` | `false` | Every `model_settings.model` in a converter-rendered pipeline ConfigMap carries a 40-hex `revision:` (Hugging Face Hub weights can be pickles, and an unpinned repo is mutable). Enforced by the `htrflow-batch-model-revision-<ns>` ClusterPolicy when `security.policies.enabled`, which also refuses any key beside `model_settings` in a step that loads a model — htrflow merges such a key over `model_settings`, so it could unpin the model |
| `security.policies.enabled` | `false` | Render the three `templates/policies/` ClusterPolicies (digest pin, allow-list, model revision), all `Enforce`. **Kyverno must be installed** (see [Prerequisites](../getting-started/index.md)) — without it these are objects nothing reads, and nothing enforces the allow-list or the revision rule at all. Off by default for exactly that reason |
| `security.policies.allowDisabled` | `false` | The explicit opt-out. With `policies.enabled` false the chart refuses to render unless this is true, so an install that enforces nothing says so instead of doing it silently. Set it on a cluster without Kyverno; `values-prod.yaml` turns the policies on instead |
| `security.psaEnforce` | `baseline` | Pod Security level `make psa-labels` enforces on the namespace (warn/audit are always `restricted`). Every pod in both charts is restricted-clean — `restricted` is worth trying |
| `security.allowTagImages` | `false` | Accept a `:tag` reference for `web.image` instead of an `@sha256:` pin. Tag images get `imagePullPolicy: Always` so a re-pushed `:dev` lands on the next rollout |
| `security.verifyImages.enabled` | `false` | Renders a Kyverno `ClusterPolicy` that refuses any Pod in the namespace whose image is not cosign keyless-signed |
| `security.verifyImages.issuer` / `subject` | `""` | OIDC issuer and subject of the signing identity — both required when enabled |
| `security.verifyImages.imageReferences` | `[]` | The images the signature check applies to; Kyverno admits any image outside them unverified. Empty means every image (`"*"`), or `allowedImageRepos` with `*` appended while `security.policies.enabled` renders the allow-list policy that refuses everything else. A list you set is used as given |
| `security.verifyImages.rekorUrl` | `https://rekor.sigstore.dev` | |

`security.allowedImageRepos` and `security.requireModelRevision` are the
real inputs to those two rules: the ClusterPolicies apply to every Job, Pod
and pipeline ConfigMap the namespace admits, which a rule inside the
converter could not see. A campaigns repo's CI runs the same policies over
`rendered/` with the Kyverno CLI — see
[Campaign & Pipeline YAML](campaign-yaml.md).

!!! note "The image policies match `Job`/`Pod` directly, not their controllers"

    The two image `ClusterPolicy` objects carry
    `pod-policies.kyverno.io/autogen-controllers: none`, so Kyverno does not
    generate matching rules for a `Deployment` or `StatefulSet` — only the
    literal `Job`/`Pod` kinds are checked. `web.image` is a `Deployment`: a
    bad image there is admitted by `helm upgrade` (the `Deployment` itself
    is never matched) and only refused when its `ReplicaSet` tries to create
    a `Pod` from it. The rejection message lands on that `Pod`'s events, not
    on the `helm upgrade` that shipped the bad image — `kubectl describe
    pod` (or `kubectl get events`) in the namespace is where to look.

## NetworkPolicies (`network.*`)

`templates/network.yaml` + the read API's own policy in `templates/web.yaml`;
the narrative is in [Security](../how-it-works/security.md). They need a CNI
that enforces NetworkPolicy. Rules match by CIDR and selector only
(NetworkPolicy has no FQDN rules), which is why campaign pods get no
Hugging Face Hub egress at all — only the warm-up pod does.

| Key | Default | Description |
|-----|---------|-------------|
| `network.enabled` | `true` | Render the policies |
| `network.defaultDeny` | `true` | Namespace-wide default deny (ingress + egress) plus a DNS allow for every pod. Anything hand-applied in the namespace (including `charts/htrflow-devstack`'s pods, which that chart gives their own policies) needs its own policy |
| `network.iiifCidrs` | `["192.121.221.27/32"]` | What campaign pods may reach besides DNS and S3, on 443/80: your IIIF origin(s). The default is one IIIF origin's address — set this for your source. Volumes declared with `images:` hosted elsewhere need that host here too; `0.0.0.0/0` allows any origin and still carves out the cluster, node and API server ranges |
| `network.s3Cidrs` | `[]` | External S3 endpoint(s) for campaign and warm-up pods; the devstack's RustFS pod is selected automatically |
| `network.clusterCidrs` | `["10.42.0.0/16", "10.43.0.0/16"]` | Pod and service ranges that pods with *public* egress (the warm-up pod) must not reach. The default is a common pair of default pod and service ranges — set it to your cluster's |
| `network.nodeCidrs` | `[]` | Node addresses (same purpose); auto-detected with Helm `lookup` when empty — set for `helm template` or a kubeconfig without list-nodes permission |
| `network.apiServer.cidr` / `cidrs` / `port` | `""` / `[]` / `6443` | kube-apiserver as reached after service DNAT: `cidr` one address, `cidrs` every further API server of an HA control plane (the egress rule names them all, since DNAT may pick any). When both are empty, every address and port of the `kubernetes` Endpoints is looked up. **The web front's NetworkPolicy fails to render without one under `helm template`** |
| `network.web.ingressCidrs` | `["0.0.0.0/0"]` | Who may reach the web front's port 8081, matched on the client's own address (the Service sets `externalTrafficPolicy: Local`, so NodePort traffic is not SNAT'd; do not list the node range for its sake). The default is any client that can reach the node. The default, an empty list and any entry wider than `/8` all need `network.web.allowPublicIngress` |
| `network.web.ingressFrom` | `[]` | NetworkPolicy peers allowed to reach port 8081 *instead of* `network.web.ingressCidrs`, for `web.ingress.*` mode: `namespaceSelector` / `podSelector` objects naming the ingress controller's pods. Selectors only: an `ipBlock` is refused, and so is a selector that selects everything (`{}`, an empty `matchLabels` or `matchExpressions`). When set, the `ingressCidrs` guards are skipped, since the pod sees only the controller's address; who may reach the front is then the controller's allow-list |
