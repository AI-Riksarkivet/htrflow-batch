# Chart Values

`charts/htrflow-batch` (version **0.4.0**) — one chart for the Kueue queue
objects, the model-cache PVC, the web front (campaign browser, Universal
Viewer and the read-only status API in one Deployment) and the
NetworkPolicies. The PoC-only support infrastructure (RustFS, an in-cluster
registry, the NVIDIA device plugin) lives in the separate
`charts/htrflow-devstack` chart. Campaigns themselves are not rendered by
either chart: they are Indexed Jobs rendered by `packages/converter` from a
campaigns repo and applied with `kubectl` or Argo CD.
Source: [`charts/htrflow-batch/values.yaml`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/charts/htrflow-batch/values.yaml);
every key is declared in `values.schema.json` (unknown keys and wrong types
are rejected at install time).

!!! warning "Upgrading: `--reset-then-reuse-values`"

    Always `helm upgrade … --reset-then-reuse-values` (or pass a full values
    file). Plain `--reuse-values` keeps the *old* chart's defaults; it once
    rendered every NetworkPolicy away, and the chart now fails loudly when
    `.Values.network` is missing. Upgrade notes are in the
    [chart README](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/charts/htrflow-batch/README.md#upgrading).

## Core

| Key | Default | Description |
|-----|---------|-------------|
| `s3.existingSecret` | `htr-batch-s3` | Secret in the release namespace. Pods read the key **`credentials`** (AWS ini: `[default] aws_access_key_id / aws_secret_access_key`) as a file mounted at `/secrets/s3/credentials` via `AWS_SHARED_CREDENTIALS_FILE`, plus the non-secret `S3_BUCKET` and optional `S3_ENDPOINT` as env. **Nothing is injected with `envFrom`**; only tooling (compose, the devStack init Job, the RustFS server) reads `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` keys directly. See [Security](../development/security.md) |
| `s3.bucket` | `htr-results` | Results bucket name; created and policied by the devStack `rustfs-init` hook. Keep it equal to the converter's `s3_secret`/bucket config so the two sides wire together by name |
| `publicResultsBase` | `""` | **Required** — browser-reachable URL base for published results (viewer manifests and the read API's `resultsBase` embed it) |

## Model cache (`modelCache.*`)

Renders the PVC that a pipeline's warm-up Job writes and campaign Jobs mount
read-only — both rendered by the converter, referencing this PVC by
`converter.yaml`'s `data_pvc`. Kept on uninstall
(`helm.sh/resource-policy: keep`).

| Key | Default | Description |
|-----|---------|-------------|
| `modelCache.create` | `true` | `false` = a PVC named `modelCache.name` already exists (hand-made, or adopt it — see the chart README) |
| `modelCache.name` | `htr-test-data` | PVC name; must match `converter.yaml`'s `data_pvc` |
| `modelCache.size` | `30Gi` | |
| `modelCache.storageClass` | `""` | `""` = cluster default (the k3s PoC uses `local-path`) |
| `modelCache.accessModes` | `[ReadWriteOnce]` | RWO pins every pod to the node holding the volume — fine on one GPU node, a constraint beyond it |

## Queue (`queue.*`)

| Key | Default | Description |
|-----|---------|-------------|
| `queue.name` | `htr-batch` | LocalQueue name; ClusterQueue is `<name>-cq`, admitting LocalQueues from the release namespace only. Must match `converter.yaml`'s `queue` |
| `queue.flavor` | `default-flavor` | ResourceFlavor |
| `queue.resources` | cpu 4 / memory 8Gi / nvidia.com/gpu 1 | Covered quotas — every resource an index's pod requests must be listed. The default admits exactly one campaign index as the converter renders it (requests cpu 4 / 8 Gi / 1 GPU); raise it to run more volumes in parallel. Indexes stuck `queued` with an idle GPU usually mean a dead Kueue controller, not a busy GPU |

## Web front (`web.*`)

Renders a ServiceAccount, a Role (get/list/watch on `jobs`, `pods`,
`configmaps` — this namespace only, never cluster-wide) and RoleBinding, and
the `htrflow-web` Deployment + Service (NodePort). One container serves the
campaign browser at `/`, Universal Viewer at `/uv.html` and the read API at
`/api/v1/…`; it is the one pod in this chart that keeps its ServiceAccount
token, because it is the Kubernetes API client the browser reads through,
computing everything live — there is no status document written by anything
in this system any more. Always rendered — there is no `enabled` flag.

| Key | Default | Description |
|-----|---------|-------------|
| `web.image` | `docker.io/riksarkivet/htrflow-web@sha256:000…` | **Must be digest-pinned** unless `security.allowTagImages`. The all-zero default renders but cannot pull: set the digest of the image you built (`make poc-push` prints it) |
| `web.nodePort` | `30800` | NodePort; the container listens on 8081 |
| `web.resources` | requests cpu 50m / 128Mi, limits cpu 500m / 256Mi | |

The app sends `X-Content-Type-Options: nosniff`, `Referrer-Policy:
strict-origin-when-cross-origin` and `Content-Security-Policy:
frame-ancestors 'none'` on every response (audit S4; script/style/connect
sources stay governed by the build's own CSP meta). `/config.js`, built into
the image, sets `window.API_BASE = "/api/v1"` — same-origin, because the
same process serves both (see [Campaign Browser](frontend.md)).

## Trust boundary (`security.*`)

| Key | Default | Description |
|-----|---------|-------------|
| `security.allowedImageRepos` | `[]` | Repository prefixes a Job or Pod in the namespace may pin, matched on a path boundary before `@sha256:`. Enforced by the `htrflow-batch-images-allowed-<ns>` ClusterPolicy when `security.policies.enabled`; empty = the policy is not rendered. Also the default for `verifyImages.imageReferences` |
| `security.requireModelRevision` | `false` | Every `model_settings.model` in a converter-rendered pipeline ConfigMap carries a 40-hex `revision:`. Enforced by the `htrflow-batch-model-revision-<ns>` ClusterPolicy when `security.policies.enabled` |
| `security.policies.enabled` | `false` | Render the three `templates/policies/` ClusterPolicies (digest pin, allow-list, model revision), all `Enforce`. **Kyverno must be installed** (`make install-kyverno`; ai-dev story I04) — without it these are objects nothing reads, and nothing enforces the allow-list or the revision rule at all |
| `security.psaEnforce` | `baseline` | Pod Security level `make psa-labels` enforces on the namespace (warn/audit are always `restricted`). Every pod in both charts is restricted-clean as of 0.3.0 — `restricted` is worth trying |
| `security.allowTagImages` | `false` | Accept a `:tag` reference for `web.image` instead of an `@sha256:` pin. Tag images get `imagePullPolicy: Always` so a re-pushed `:dev` lands on the next rollout |
| `security.verifyImages.enabled` | `false` | Renders a Kyverno `ClusterPolicy` (Kyverno ≥ 1.10 must be installed) that refuses any Pod in the namespace whose image is not cosign keyless-signed |
| `security.verifyImages.issuer` / `subject` | `""` | OIDC issuer and subject of the signing identity — both required when enabled |
| `security.verifyImages.imageReferences` | `[]` | Defaults to `allowedImageRepos` with `*` appended, or `"*"` when that is empty |
| `security.verifyImages.rekorUrl` | `https://rekor.sigstore.dev` | |

!!! note "Where these two rules have lived"

    Through 0.2.0 they gated what the old CronJob controller would submit.
    From 0.3.0 they were the campaigns repo's `converter.yaml` keys
    `allowed_image_repos` / `require_model_revision`, and this chart's
    values were documentation. As of **0.6.0** the converter has dropped
    them and these values are the real inputs: a rule the converter applies
    only ever sees what the converter rendered, while the ClusterPolicies
    apply to every Job, Pod and pipeline ConfigMap the namespace admits.
    A `converter.yaml` still carrying either key is now a validation error
    pointing here ([Campaign & Pipeline YAML](campaign-yaml.md)); a
    campaigns repo's CI runs these same policies over `rendered/` with the
    Kyverno CLI.

## NetworkPolicies (`network.*`)

`templates/network.yaml` + the read API's own policy in `templates/web.yaml`;
the narrative is in [Security](../development/security.md). Rules match by
CIDR and selector only (no FQDN rules on kube-router), which is why campaign
pods get no HF Hub egress at all — only the warm-up pod does.

| Key | Default | Description |
|-----|---------|-------------|
| `network.enabled` | `true` | Render the policies |
| `network.defaultDeny` | `true` | Namespace-wide default deny (ingress + egress) plus a DNS allow for every pod. Anything hand-applied in the namespace (including `charts/htrflow-devstack`'s pods) needs its own policy |
| `network.iiifCidrs` | `["192.121.221.27/32"]` | What campaign pods may reach besides DNS and S3 (default: `lbiiif.riksarkivet.se`), on 443/80 |
| `network.s3Cidrs` | `[]` | External S3 endpoint(s) for campaign and warm-up pods; the devStack RustFS pod is selected automatically |
| `network.clusterCidrs` | `["10.42.0.0/16", "10.43.0.0/16"]` | Pod and service ranges that pods with *public* egress (the warm-up pod) must not reach |
| `network.nodeCidrs` | `[]` | Node addresses (same purpose); auto-detected with Helm `lookup` when empty — set for `helm template` |
| `network.apiServer.cidr` / `port` | `""` / `6443` | kube-apiserver as reached after service DNAT; auto-detected from the `kubernetes` Endpoints when empty. **The web front's NetworkPolicy fails to render without it under `helm template`** |
| `network.web.ingressCidrs` | `["0.0.0.0/0"]` | Who may reach the web front's port 8081 (NodePort traffic arrives SNAT'd from the node) |

## Removed in 0.4.0

The whole `viewer` values block: `enabled`, `image`, `nodePort` (now
`web.nodePort`), `defaultManifest` (deprecated since the campaign browser
landed) and `securityHeaders.enabled` (the headers are unconditional now,
sent by the app). `network.viewer` is renamed `network.web`. The nginx Deployment, its Service, its
`config.js`/`default.conf` ConfigMap and its `/api/` proxy are gone: the
read API image now carries the site.

## Removed in 0.3.0

The old GitOps CronJob controller (its own template file, its
ServiceAccount/Role/RoleBinding and Lease, its own values block), chart-
rendered pipeline ConfigMaps (`.Values.pipelines`), the example smoke Job
(`.Values.exampleJob`), and `devStack.*` (moved to its own chart,
`charts/htrflow-devstack` — see [Local k3s development](../development/local-k3s.md)),
except `devStack.allowTagImages` which moved to `security.allowTagImages`
(it gates this chart's own control-plane images, not anything devstack-only).
None of it has a replacement value in this chart — the converter and the
campaigns repo's own CI took over what it did. Full migration table:
[chart README](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/charts/htrflow-batch/README.md#upgrading).
