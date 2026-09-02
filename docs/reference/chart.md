# Chart Values

`charts/htrflow-batch` (version **0.3.0**) — one chart for the Kueue queue
objects, the model-cache PVC, the viewer, the read-only status API and the
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

## Read API (`api.*`)

Renders a ServiceAccount, a Role (get/list/watch on `jobs`, `pods`,
`configmaps` — this namespace only, never cluster-wide) and RoleBinding, and
the `htrflow-api` Deployment + Service (ClusterIP, no NodePort — reached
only through the viewer's `/api/` proxy). It is the one pod in this chart
that keeps its ServiceAccount token: it is the Kubernetes API client the
campaign browser reads through, computing everything live — there is no
status document written by anything in this system any more. Always
rendered — there is no `enabled` flag.

| Key | Default | Description |
|-----|---------|-------------|
| `api.image` | `docker.io/riksarkivet/htrflow-api@sha256:000…` | **Must be digest-pinned** unless `security.allowTagImages`. The all-zero default renders but cannot pull |
| `api.resources` | requests cpu 50m / 128Mi, limits cpu 500m / 256Mi | |

## Trust boundary (`security.*`)

| Key | Default | Description |
|-----|---------|-------------|
| `security.allowedImageRepos` | `[]` | Documentation/consistency only in this chart — the enforced allow-list lives in the campaigns repo's `converter.yaml` (`allowed_image_repos`); `htrflow-campaigns validate` warns, not the chart |
| `security.requireModelRevision` | `false` | Same: mirrors `converter.yaml`'s `require_model_revision` for documentation purposes |
| `security.psaEnforce` | `baseline` | Pod Security level `make psa-labels` enforces on the namespace (warn/audit are always `restricted`). Every pod in both charts is restricted-clean as of 0.3.0 — `restricted` is worth trying |
| `security.allowTagImages` | `false` | Accept `:tag` references for `viewer.image` / `api.image` instead of `@sha256:` pins. Tag images get `imagePullPolicy: Always` so a re-pushed `:dev` lands on the next rollout |
| `security.verifyImages.enabled` | `false` | Renders a Kyverno `ClusterPolicy` (Kyverno ≥ 1.10 must be installed) that refuses any Pod in the namespace whose image is not cosign keyless-signed |
| `security.verifyImages.issuer` / `subject` | `""` | OIDC issuer and subject of the signing identity — both required when enabled |
| `security.verifyImages.imageReferences` | `[]` | Defaults to `allowedImageRepos` with `*` appended, or `"*"` when that is empty |
| `security.verifyImages.rekorUrl` | `https://rekor.sigstore.dev` | |

!!! note "`allowedImageRepos`/`requireModelRevision` moved to the campaigns repo"

    Through 0.2.0 these chart values gated what the old CronJob controller
    would submit. As of 0.3.0 the converter runs entirely outside the cluster (in the
    campaigns repo's own CI), so the enforcement point moved with it:
    set `allowed_image_repos` / `require_model_revision` in that repo's
    `converter.yaml` ([Campaign & Pipeline YAML](campaign-yaml.md)).

## NetworkPolicies (`network.*`)

`templates/network.yaml` + the read API's own policy in `templates/api.yaml`;
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
| `network.apiServer.cidr` / `port` | `""` / `6443` | kube-apiserver as reached after service DNAT; auto-detected from the `kubernetes` Endpoints when empty. **The read API's NetworkPolicy fails to render without it under `helm template`** |
| `network.viewer.ingressCidrs` | `["0.0.0.0/0"]` | Who may reach the viewer's port 8080 (NodePort traffic arrives SNAT'd from the node) |

## Viewer (`viewer.*`)

| Key | Default | Description |
|-----|---------|-------------|
| `viewer.enabled` | `true` | Deploy the viewer (campaign browser at `/`, UV4 at `/uv.html`, `/api/` proxied to the read API); Pod Security restricted, uid 101, no SA token |
| `viewer.image` | `docker.io/riksarkivet/htrflow-batch-viewer@sha256:000…` | **Must be digest-pinned** unless `security.allowTagImages`. The all-zero default renders but cannot pull: set the digest of the image you built (`make viewer-image`, then `docker push` prints it) |
| `viewer.nodePort` | `30800` | NodePort; the container listens on 8080 |
| `viewer.defaultManifest` | `""` | Deprecated: when set, `/` 302-redirects into UV instead of the campaign browser |
| `viewer.securityHeaders.enabled` | `true` | nginx sends `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Content-Security-Policy: frame-ancestors 'none'` (script/style/connect sources stay governed by the build's own CSP meta) |

The viewer's `config.js` (served same-origin, so it passes the SPA's CSP)
sets `window.API_BASE = "/api/v1"`, which the frontend resolves on every
fetch (see [Campaign Browser](frontend.md)).

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
