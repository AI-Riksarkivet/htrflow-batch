# Chart Values

`charts/htrflow-batch` holds the Kueue queue objects, the model-cache PVC,
the web front, the NetworkPolicies and the Kyverno policies. Campaigns are
not in it: `htrflow-campaigns apply` applies them. The development stack is
the separate `charts/htrflow-devstack` chart. Every key is declared in
`values.schema.json`, so unknown keys and wrong types are refused; the
generated table of every key is [Configuration](configuration.md). Source:
[`values.yaml`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/charts/htrflow-batch/values.yaml).

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
| `s3.existingSecret` | `htr-batch-s3` | The S3 Secret you create in the release namespace: key `credentials` (AWS ini), mounted as a file, plus `S3_BUCKET` (the bucket's name: the chart has no value for it) and optional `S3_ENDPOINT`. Must match `converter.yaml`'s `s3_secret` ([Deploy](../getting-started/deploy.md)) |
| `hfToken.existingSecret` | `""` | The Hugging Face token Secret (key `token`) a warm-up may read, for a private or gated model. Must match `converter.yaml`'s `hf_token_secret`. Empty = none |
| `publicResultsBase` | `""` | **Required.** The browser-reachable base of the results bucket. Must match `converter.yaml`'s `public_results_base` ([View Results](../getting-started/viewing.md)) |

## Model cache (`modelCache.*`)

Renders the PVC that a pipeline's warm-up Job writes and campaign Jobs mount
read-only — both rendered by the converter, referencing this PVC by
`converter.yaml`'s `data_pvc`. Kept on uninstall
(`helm.sh/resource-policy: keep`). The cache is never evicted: a pipeline's
models stay until the PVC is dropped.

| Key | Default | Description |
|-----|---------|-------------|
| `modelCache.create` | `true` | `false` = a PVC named `modelCache.name` already exists (hand-made, or adopt it — see the chart README) |
| `modelCache.name` | `htr-test-data` | PVC name; must match `converter.yaml`'s `data_pvc`. With `security.policies.enabled` it is the one PVC a campaign or warm-up Job may mount |
| `modelCache.size` | `30Gi` | |
| `modelCache.storageClass` | `""` | `""` = the cluster's default StorageClass |
| `modelCache.accessModes` | `[ReadWriteOnce]` | RWO pins every pod to the node holding the volume — fine on one GPU node, a scheduling constraint beyond it, and with `queue.flavors` on several nodes a flavor whose nodes cannot reach it runs nothing; use an RWX class (or a per-node cache) to scale out |

## Queue (`queue.*`)

| Key | Default | Description |
|-----|---------|-------------|
| `queue.name` | `htr-batch` | LocalQueue name, in the release namespace. Must match `converter.yaml`'s `queue` |
| `queue.clusterQueueName` | `""` | The ClusterQueue the LocalQueue points at; `""` = `<name>-cq` |
| `queue.createClusterQueue` | `true` | Create that ClusterQueue, admitting LocalQueues from the release namespace only. `false` = it exists already (another release's, or the cluster's own) and must admit this namespace itself |
| `queue.flavor` | `default-flavor` | The ResourceFlavor the created ClusterQueue's quota is in |
| `queue.createFlavor` | `true` | Create that ResourceFlavor. The flavor, the ClusterQueue and the priority classes are cluster-scoped, so a second release needs other names or `create…: false` |
| `queue.createPriorityClasses` | `true` | Create one `WorkloadPriorityClass` per `queue.priorityClasses` entry. `false` = the classes are the cluster's; the list then only names them, and `converter.yaml`'s `priority_classes` must still repeat it |
| `queue.priorityClasses` | `htr-interactive` 1000, `htr-bulk` 0, `htr-idle` -10 | One `WorkloadPriorityClass` per entry: the names a campaign's `priority:` may use. `converter.yaml`'s `priority_classes` must repeat them ([Queueing](../how-it-works/queueing.md)) |
| `queue.resources` | cpu 4 / memory 8Gi / nvidia.com/gpu 1 | The ClusterQueue's quota. Every resource a pod requests must be listed. The default admits one campaign index at a time; raise it to run more volumes in parallel |
| `queue.flavors` | `[]` | More than one sort of GPU: one ResourceFlavor per entry (`name`, `nodeLabels`, optional `nodeTaints`, and a `quota` of `cpu`, `memory` and `nvidia.com/gpu`, each above 0), in one resource group Kueue tries in this order, so list the cheapest card first. Every two flavors must name a label key in common with different values. A flavor tolerates its own taints. Set, it replaces `queue.flavor` and `queue.resources` (see the upgrade note in [Several sorts of GPU](../how-it-works/queueing.md#several-sorts-of-gpu)); with `createFlavor: false` the flavors are the cluster's, referenced by name. `converter.yaml`'s `flavors` must repeat the names and node labels, and `apply` checks that they do |

## Web front (`web.*`)

Renders the `htrflow-web` Deployment and Service, and its ServiceAccount
with a namespaced Role: `get`/`list` on `jobs` and `pods`, and
`get`/`list`/`create`/`patch` on `configmaps` for the one write it makes,
each campaign's status record. No `watch`, no `delete`, no write to a Job or
Pod. It is the one pod here that keeps its ServiceAccount token, and it
also reads the results bucket for progress, so its NetworkPolicy has an S3
egress rule. Always rendered. What it serves is in
[Web front & read API](web.md).

| Key | Default | Description |
|-----|---------|-------------|
| `web.image` | `docker.io/riksarkivet/htrflow-web@sha256:…` | **Must be digest-pinned** unless `security.allowTagImages`. Pin a manifest-list digest, so it resolves on any architecture ([Releasing](../development/releasing.md)) |
| `web.nodePort` | `30800` | NodePort; the container listens on 8081. Answered only on the node running the pod (`externalTrafficPolicy: Local`). Unused with `web.service.type: ClusterIP` |
| `web.service.type` | `NodePort` | `NodePort` is the browser's direct way in. `ClusterIP` is for an ingress controller in front (`web.ingress.*`): no node port and no `externalTrafficPolicy`, since the pod then sees only the controller's address |
| `web.ingress.enabled` | `false` | Renders an Ingress for the web front. Refused unless `web.service.type` is `ClusterIP` and `web.ingress.host` and `network.web.ingressFrom` are set. The Ingress carries no authentication, and the `network.web.ingressCidrs` guards do not apply in this mode: set the controller's allow-list (see [Deploy → Web front access](../getting-started/deploy.md#web-front-access)) |
| `web.ingress.host` | `""` | The hostname the Ingress routes; required with `web.ingress.enabled` |
| `web.ingress.className` | `""` | `ingressClassName`; `""` = the cluster's default IngressClass |
| `web.ingress.tlsSecretName` | `""` | TLS Secret for `web.ingress.host`, terminated at the Ingress; `""` = no `tls` block |
| `web.ingress.annotations` | `{}` | Annotations on the Ingress, e.g. the controller's source-range allow-list (`nginx.ingress.kubernetes.io/whitelist-source-range`) |
| `web.resources` | requests cpu 50m / 128Mi, limits cpu 500m / 256Mi | |
| `web.internalResultsBase` | `""` | Where this pod reaches the bucket to read progress. Empty = `publicResultsBase`. Set it when that address does not work from inside the cluster, or the page shows no progress ([View Results](../getting-started/viewing.md)) |

Its security headers and `/config.js` are described in
[Web front & read API](web.md#content-security-policy).

## Apply identity (`apply.*`)

| Key | Default | Description |
|-----|---------|-------------|
| `apply.rbac.enabled` | `false` | Renders the ServiceAccount `htrflow-campaigns` and a namespaced Role for an in-cluster apply (the Argo CD hook): write and delete on `jobs` and `configmaps`, `list`/`patch` on Kueue Workloads, and the apply Lease. Plus one read-only ClusterRole: `get` on this release's LocalQueue, ClusterQueue and ResourceFlavors, by name, so `apply` can check `converter.yaml`'s `flavors` against them. Off by default, since an idle account that may delete Jobs is a liability. See [htrflow-campaigns CLI](cli.md#with-argo-cd) |
| `apply.gitCidrs` | `[]` | The git host the Argo CD hook clones the campaigns repo from, by address (a NetworkPolicy cannot name a host): an egress rule for the `app=htrflow-campaigns` pod. Empty = no git egress; the pod reaches only DNS and the API server, which is all `apply` on a local checkout needs. See [htrflow-campaigns CLI → The hook manifest](cli.md#the-hook-manifest) |
| `apply.gitPorts` | `[443]` | Ports of that egress rule. At least one: a rule with no ports would open every port |

## Trust boundary (`security.*`)

| Key | Default | Description |
|-----|---------|-------------|
| `security.allowedImageRepos` | `[]` | Repository prefixes any Job or Pod in the namespace may pin, enforced by a ClusterPolicy when `security.policies.enabled`; empty = no policy. Namespace-wide on purpose, so supporting workloads (the devstack's RustFS) need their prefix here too |
| `security.jobImageRepos` | `[]` | The exact repositories (the part before `@sha256:`) a campaign or warm-up Job's images may come from, enforced by the `job-shape` policy. `allowedImageRepos` admits every repository the namespace runs, the web and converter images included; this narrows the converter's Jobs to the wrapper's. Empty = no narrowing |
| `security.requireModelRevision` | `false` | Every model in a pipeline ConfigMap must carry a 40-hex revision, enforced by a ClusterPolicy when `security.policies.enabled`. The rules and messages are in [Campaign & Pipeline YAML](campaign-yaml.md#pipeline-file-pipelinesidyaml) |
| `security.policies.enabled` | `false` | Render the Kyverno ClusterPolicies, all `Enforce`: digest pin, allow-list, model revision, `rbac-scope` and `job-shape` ([Security](../how-it-works/security.md)). **Kyverno must be installed.** The chart and converter must be the same release, since `job-shape` compares the Jobs' scripts |
| `security.policies.allowDisabled` | `false` | With the policies off, the chart refuses to render unless this is `true`, so an install that enforces nothing says so |
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
| `network.iiifCidrs` | `[]` | What campaign pods may reach besides DNS and S3, on 443/80: your IIIF origin(s). **Required**: there is no default, and an empty list is refused, since it would fetch nothing. Volumes declared with `images:` hosted elsewhere need that host here too; `0.0.0.0/0` allows any origin. Every range here, in `network.s3Cidrs` and in `apply.gitCidrs` loses the internal ranges inside it — cluster, node, API server, link-local, loopback and `network.privateCidrs` ([Security](../how-it-works/security.md)) |
| `network.s3Cidrs` | `[]` | External S3 endpoint(s) for campaign pods and the web front (its progress reader), on `network.s3Ports` (default `[443]`); the warm-up has no S3 rule |
| `network.s3InNamespace` | `true` | The bucket is the devstack's in-namespace RustFS: campaign pods and the web front may reach any `app: rustfs` pod on 9000. `false` drops that rule (any pod carrying the label would otherwise be a destination), and then an empty `network.s3Cidrs` is refused, since a campaign pod with no route to S3 fails every volume after its GPU time. `values-prod.yaml` sets `false` |
| `network.clusterCidrs` | `["10.42.0.0/16", "10.43.0.0/16"]` | Pod and service ranges that pods with *public* egress (the warm-up pod) must not reach. The default is a common pair of default pod and service ranges — set it to your cluster's. An empty list is refused. `values-prod.yaml` empties it, so a production install must name its own |
| `network.privateCidrs` | `["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"]` | Private ranges carved out of every egress range that holds one, on top of the cluster, node, API server, link-local and loopback ranges: the three private blocks and carrier-grade NAT space, which some clouds and overlay networks use internally. A range named inside one stays reachable |
| `network.nodeCidrs` | `[]` | Node addresses (same purpose); auto-detected with Helm `lookup` when empty — set for `helm template` or a kubeconfig without list-nodes permission |
| `network.apiServer.cidr` / `cidrs` / `port` | `""` / `[]` / `6443` | kube-apiserver as reached after service DNAT: `cidr` one address, `cidrs` every further API server of an HA control plane (the egress rule names them all, since DNAT may pick any). When both are empty, every address and port of the `kubernetes` Endpoints is looked up. **The web front's NetworkPolicy fails to render without one under `helm template`** |
| `network.web.ingressCidrs` | `["0.0.0.0/0"]` | Who may reach the web front's port 8081, matched on the client's own address (the Service sets `externalTrafficPolicy: Local`, so NodePort traffic is not SNAT'd; do not list the node range for its sake). The default is any client that can reach the node. The default, an empty list and any entry wider than `/8` all need `network.web.allowPublicIngress` |
| `network.web.ingressFrom` | `[]` | NetworkPolicy peers allowed to reach port 8081 *instead of* `network.web.ingressCidrs`, for `web.ingress.*` mode: `namespaceSelector` / `podSelector` objects naming the ingress controller's pods. Selectors only: an `ipBlock` is refused, and so is a selector that selects everything (`{}`, an empty `matchLabels` or `matchExpressions`). When set, the `ingressCidrs` guards are skipped, since the pod sees only the controller's address; who may reach the front is then the controller's allow-list |
