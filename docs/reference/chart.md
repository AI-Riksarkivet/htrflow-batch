# Chart Values

`charts/htrflow-batch` (version **0.2.0**) — one chart for the Kueue queue
objects, the model-cache PVC, the viewer, the GitOps reconciler, the
NetworkPolicies and the PoC dev stack. Per-volume Jobs are created at runtime
by the reconciler, never by the chart.
Source: [`charts/htrflow-batch/values.yaml`](https://github.com/carpelan/test/blob/main/charts/htrflow-batch/values.yaml);
every key is declared in `values.schema.json` (unknown keys and wrong types
are rejected at install time).

!!! warning "Upgrading: `--reset-then-reuse-values`"

    Always `helm upgrade … --reset-then-reuse-values` (or pass a full values
    file). Plain `--reuse-values` keeps the *old* chart's defaults; it once
    rendered every NetworkPolicy away, and the chart now fails loudly when
    `.Values.network` is missing. The 0.2.0 upgrade notes are in the
    [chart README](https://github.com/carpelan/test/blob/main/charts/htrflow-batch/README.md#changelog).

## Core

| Key | Default | Description |
|-----|---------|-------------|
| `s3.existingSecret` | `htr-batch-s3` | Secret in the release namespace. Pods read the key **`credentials`** (AWS ini: `[default] aws_access_key_id / aws_secret_access_key`) as a file mounted at `/secrets/s3/credentials` via `AWS_SHARED_CREDENTIALS_FILE`, plus the non-secret `S3_BUCKET` and optional `S3_ENDPOINT` as env. **Nothing is injected with `envFrom`**; `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` keys are only read by tooling (compose, the devStack init Job, the RustFS server). See [Security](../development/security.md) |
| `s3.bucket` | `htr-results` | Results bucket name; created and policied by the devStack `rustfs-init` hook |
| `publicResultsBase` | `""` | **Required** — browser-reachable URL base for published results (viewer manifests and every `status.json` URL embed it) |

Removed in 0.2.0: `image.*` and `s3.endpoint` (dead values — campaign Jobs
pin their image in the pipeline YAML; the endpoint comes from the Secret).

## Model cache (`modelCache.*`)

Renders the PVC that warm-up Jobs write and batch Jobs mount read-only, and
passes its name to the reconciler as `RECONCILER_DATA_PVC`. Kept on
uninstall (`helm.sh/resource-policy: keep`).

| Key | Default | Description |
|-----|---------|-------------|
| `modelCache.create` | `true` | `false` = a PVC named `modelCache.name` already exists (hand-made, or adopt it — see the chart README) |
| `modelCache.name` | `htr-test-data` | PVC name; also `RECONCILER_DATA_PVC` and the example Job's claim |
| `modelCache.size` | `30Gi` | |
| `modelCache.storageClass` | `""` | `""` = cluster default (the k3s PoC uses `local-path`) |
| `modelCache.accessModes` | `[ReadWriteOnce]` | RWO pins every Job to the node holding the volume — fine on one GPU node, a constraint beyond it |

## Queue (`queue.*`)

| Key | Default | Description |
|-----|---------|-------------|
| `queue.name` | `htr-batch` | LocalQueue name; ClusterQueue is `<name>-cq`, admitting LocalQueues from the release namespace only |
| `queue.flavor` | `default-flavor` | ResourceFlavor |
| `queue.resources` | cpu 4 / memory 8Gi / nvidia.com/gpu 1 | Covered quotas — every resource a Job requests must be listed. The default admits exactly one Job as `jobspec.py` builds it (requests cpu 4 / 8 Gi / 1 GPU); raise it to run Jobs in parallel. Jobs stuck `queued` with an idle GPU usually mean a dead Kueue controller, not a busy GPU |

## Pipelines (`pipelines`)

Map of `id → htrflow pipeline YAML string`, rendered as immutable ConfigMaps
`htr-pipeline-<id>`. Never change content under an existing id — mint a new
one. Campaign pipelines are managed by the reconciler from git instead; this
value is for the example Job. A pipeline declared here must be warmed by hand
(`make warmup PIPELINE=<id> IMAGE=<ref>`): the chart renders no warm-up Job.

## Per-volume Job (`job.*`)

Read by the reconciler (`RECONCILER_JOB_*`) when it builds a volume Job;
`templates/job-example.yaml` mirrors them.

| Key | Default | Description |
|-----|---------|-------------|
| `job.runtimeClassName` | `nvidia` | `""` omits `runtimeClassName` (CPU-only clusters) |
| `job.nodeSelector` | `{}` | e.g. `{nvidia.com/gpu.present: "true"}` |
| `job.tolerations` | `[]` | e.g. `[{key: nvidia.com/gpu, operator: Exists, effect: NoSchedule}]` |
| `job.minDeadlineSeconds` | `21600` | Job `activeDeadlineSeconds = max(minDeadlineSeconds, pages × secondsPerPage)`; the minimum applies when the page count is unknown |
| `job.secondsPerPage` | `30` | Measured GPU throughput on the PoC is ~13 s/page; 30 leaves headroom |
| `job.manifestMaxBytes` | `16777216` | Wrapper byte cap on the IIIF manifest (Job env `MANIFEST_MAX_BYTES`) |
| `job.fetchMaxBytes` | `67108864` | Wrapper byte cap on one page image (Job env `FETCH_MAX_BYTES`) |

## Trust boundary (`security.*`)

| Key | Default | Description |
|-----|---------|-------------|
| `security.allowedImageRepos` | `[]` | Image repositories a campaigns-repo pipeline may pin (prefix match on a path boundary, before `@sha256:`). Empty = any digest-pinned image runs on the GPU and the reconciler emits a warning in `status.json`. **Set it in production** |
| `security.requireModelRevision` | `false` | Every `model_settings.model` must carry a 40-hex `revision:` (HF weights are pickles; an unpinned repo is mutable) |
| `security.psaEnforce` | `baseline` | Pod Security level `make psa-labels` enforces on the namespace (warn/audit are always `restricted`). `baseline` is only needed while `devStack.gitDaemon` runs as root |
| `security.verifyImages.enabled` | `false` | Renders a Kyverno `ClusterPolicy` (Kyverno ≥ 1.10 must be installed) that refuses any Pod in the namespace whose image is not cosign keyless-signed |
| `security.verifyImages.issuer` / `subject` | `""` | OIDC issuer and subject of the signing identity — both required when enabled |
| `security.verifyImages.imageReferences` | `[]` | Defaults to `allowedImageRepos` with `*` appended, or `"*"` when that is empty |
| `security.verifyImages.rekorUrl` | `https://rekor.sigstore.dev` | |

## NetworkPolicies (`network.*`)

`templates/network.yaml`; the narrative is in
[Security](../development/security.md). Rules match by CIDR and selector
only (no FQDN rules on kube-router), which is why batch Jobs get no HF Hub
egress at all.

| Key | Default | Description |
|-----|---------|-------------|
| `network.enabled` | `true` | Render the policies |
| `network.defaultDeny` | `true` | Namespace-wide default deny (ingress + egress) plus a DNS allow for every pod. Anything hand-applied in the namespace needs its own policy |
| `network.iiifCidrs` | `["192.121.221.27/32"]` | What batch Jobs may reach besides DNS and S3 (default: `lbiiif.riksarkivet.se`), on 443/80 |
| `network.s3Cidrs` | `[]` | External S3 endpoint(s) for Jobs and the reconciler; the devStack RustFS pod is selected automatically |
| `network.clusterCidrs` | `["10.42.0.0/16", "10.43.0.0/16"]` | Pod and service ranges that pods with *public* egress (warm-up, reconciler, git daemon) must not reach |
| `network.nodeCidrs` | `[]` | Node addresses (same purpose); auto-detected with Helm `lookup` when empty — set for `helm template` |
| `network.apiServer.cidr` / `port` | `""` / `6443` | kube-apiserver as reached after service DNAT; auto-detected from the `kubernetes` Endpoints when empty. **The reconciler policy fails to render without it under `helm template`** |
| `network.reconciler.egressCidrs` | GitHub's four `git` ranges | Where the campaigns repo lives, on 443/22/9418. Empty = no public egress (in-cluster git daemon only) |
| `network.reconciler.extraEgress` | `[]` | Raw egress rules appended verbatim (a git daemon you run yourself; `devStack.gitDaemon` adds its own rule) |
| `network.viewer.ingressCidrs` | `["0.0.0.0/0"]` | Who may reach the viewer's port 8080 (NodePort traffic arrives SNAT'd from the node) |

## Viewer (`viewer.*`)

| Key | Default | Description |
|-----|---------|-------------|
| `viewer.enabled` | `true` | Deploy the viewer (campaign browser at `/`, UV4 at `/uv.html`); Pod Security restricted, uid 101, no SA token |
| `viewer.image` | `docker.io/riksarkivet/htrflow-batch-viewer@sha256:000…` | **Must be digest-pinned** unless `devStack.allowTagImages`. The all-zero default renders but cannot pull: set the digest of the image you built (`make viewer-image`, then `docker push` prints it). The published `:latest` still predates the 8080/campaign-browser layout |
| `viewer.nodePort` | `30800` | NodePort; the container listens on 8080 |
| `viewer.defaultManifest` | `""` | Deprecated: when set, `/` 302-redirects into UV instead of the campaign browser |
| `viewer.statusBase` | `""` | Browser-reachable results base the SPA reads `status/status.json` from; defaults to `publicResultsBase`. Served to the browser as `/config.js` (`window.STATUS_URL`) from the viewer ConfigMap — same-origin, so it passes the SPA's CSP |
| `viewer.securityHeaders.enabled` | `true` | nginx sends `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Content-Security-Policy: frame-ancestors 'none'` (script/style/connect sources stay governed by the build's own CSP meta) |

## Reconciler (`reconciler.*`)

Renders a ServiceAccount, a Role (jobs get/list/create/delete; configmaps
get/create; pods get/list; pods/log get; `coordination.k8s.io/leases` create,
plus get/update on `htr-reconciler`), a RoleBinding and the `htr-reconciler`
CronJob (`concurrencyPolicy: Forbid`, `startingDeadlineSeconds: 120`,
`activeDeadlineSeconds` = `tickDeadlineSeconds`, `backoffLimit: 0`, namespace
via the downward API, S3 credentials as the mounted `credentials` file, an
emptyDir at `/tmp` for the clone). The env it renders is listed in
[Reconciler → Settings](reconciler.md#settings-environment).

| Key | Default | Description |
|-----|---------|-------------|
| `reconciler.enabled` | `false` | Deploy the CronJob + RBAC |
| `reconciler.image` | `""` | **Required** when enabled; digest-pinned unless `devStack.allowTagImages` |
| `reconciler.campaignsRepoUrl` | `""` | **Required**: `https://` (anonymous, or token via `gitTokenSecret`) or `git://` URL of the campaigns repo, cloned shallow every tick by dulwich |
| `reconciler.campaignsRepoWebUrl` | `""` | Browsable URL shown in the campaign browser header (`campaigns_repo_url` in `status.json`); falls back to `campaignsRepoUrl` |
| `reconciler.schedule` | `*/5 * * * *` | Tick cadence |
| `reconciler.tickSeconds` | `300` | **Must equal the schedule interval**: emitted as `tick_seconds`, the STALE threshold on the page is 3× it |
| `reconciler.tickDeadlineSeconds` | `600` | CronJob `activeDeadlineSeconds`; also the Lease duration and the clamp on the git timeout |
| `reconciler.gitTimeoutSeconds` | `300` | Socket timeout for the campaigns clone/fetch (clamped to `tickDeadlineSeconds`) |
| `reconciler.gitTokenSecret.name` / `key` | `""` / `token` | Optional Secret holding a read-only token for an `https://` campaigns URL (`GIT_TOKEN`, sent as `x-access-token`). Never put the token in the URL |
| `reconciler.window` | `20` | Max Jobs in flight (pending, running or Terminating) across all campaigns |
| `reconciler.attemptCap` | `3` | Attempts per (pipeline, volume) — and per pipeline warm-up — before `needs-attention` |
| `reconciler.maxValidationsPerTick` | `50` | Source manifests fetched per tick (8 concurrent, 10 s each); the rest wait for a later tick |
| `reconciler.fetchMaxBytes` | `16777216` | Byte cap on the reconciler's own manifest fetch |
| `reconciler.publicResultsBase` | `""` | Defaults to the global `publicResultsBase` |

## Dev stack (`devStack.*`) — PoC only, all off by default

Never enable any of these next to real data; see
[Local k3s development](../development/local-k3s.md) for the loop they serve.

| Key | Default | Description |
|-----|---------|-------------|
| `devStack.allowTagImages` | `false` | Accept `:tag` references for `reconciler.image` / `viewer.image`. Tag images get `imagePullPolicy: Always` so a re-pushed `:dev` lands on the next rollout |
| `devStack.rustfs.enabled` | `false` | In-cluster S3 (RustFS, uid 10001, restricted) on NodePort `nodePortS3` (30900); renders the S3 Secret (`resource-policy: keep`) and a `rustfs-data` PVC |
| `devStack.rustfs.accessKey` / `secretKey` | `""` | Root credentials. Empty = generated once (32 random chars) and re-read from the existing Secret on every upgrade; `helm template` renders fresh values each time |
| `devStack.rustfs.console.enabled` / `nodePort` | `false` / `30901` | Admin console; off = `RUSTFS_CONSOLE_ENABLE=false` and no NodePort |
| `devStack.rustfs.storage.size` / `storageClass` | `5Gi` / `local-path` | ≈ 2–4 k pages; nothing is ever deleted by the platform |
| `devStack.rustfs.init.enabled` | `true` | Post-install/upgrade hook Job (`amazon/aws-cli`, digest-pinned) that creates the buckets and applies the bucket policy + CORS, idempotently |
| `devStack.rustfs.init.corsOrigins` | `["*"]` | Tighten to the viewer origin beyond the PoC |
| `devStack.rustfs.publicLogs` | `true` | Anonymous GET on `status/logs/*`. `<pipeline>/<volume>/*`, `sources/*` and `status/status.json` are always public; `status/attempts.json`, `validation.json`, `failures/*`, `warmup/*` always need credentials. Listing is always denied |
| `devStack.registry.enabled` | `false` | In-cluster image registry in its own `registry` namespace (kept on uninstall), NodePort `nodePort` (30500), unauthenticated by design |
| `devStack.registry.image` | digest-pinned `registry` | |
| `devStack.registry.runAsUser` | `1000` | Read-only rootfs; a data PVC written by an older root-running registry needs a one-time `chown -R 1000:1000`. `0` keeps root |
| `devStack.registry.storage.size` / `storageClass` | `60Gi` / `local-path` | ≈ 5 GPU wrapper images; no GC of its own |
| `devStack.nvidiaDevicePlugin.enabled` | `false` | Renders RuntimeClass `nvidia` and the `kube-system` device-plugin DaemonSet (`image` digest-pinned). Hand-applied copies must be adopted first |
| `devStack.gitDaemon.enabled` | `false` | In-cluster `git://` daemon serving a bare campaigns repo seeded from `<bucket>/<repo>.git` in RustFS (needs `devStack.rustfs.enabled` or an explicit `seedUrl`). The one pod that is not restricted-clean (`alpine/git` installs `git-daemon` at start as root) |
| `devStack.gitDaemon.image` / `bucket` / `repo` / `seedUrl` | `alpine/git@sha256:…` / `git-repos` / `campaigns-local` / `""` | `seedUrl` defaults to `http://rustfs.<ns>.svc.cluster.local:9000/<bucket>/<repo>.git`; re-seed with `kubectl rollout restart deploy/git-daemon` |

## Example Job (`exampleJob.*`)

| Key | Default | Description |
|-----|---------|-------------|
| `exampleJob.enabled` | `false` | Render the `htr-vol-301` smoke Job (suspended) wired to the devStack endpoints; warm its pipeline first |
| `exampleJob.image` / `manifestUrl` / `pipelineId` | `""` / `""` / `demo-v1` | Image (not digest-gated: a replay aid), source manifest, and pipeline ConfigMap |
