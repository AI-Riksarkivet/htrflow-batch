# htrflow-batch (Helm chart)

Kueue-gated batch HTR platform around the htrflow image: queues, pipeline
ConfigMaps, the model-cache PVC, the GitOps reconciler, NetworkPolicies and a
results viewer. Per-volume Jobs are submitted at runtime by the reconciler,
not by this chart — `templates/job-example.yaml` is a smoke artifact for PoC
replay only (`exampleJob.enabled`, off by default).

Every value is declared in `values.schema.json` (unknown keys and wrong types
are rejected); `values.yaml` documents each one and
[docs/reference/chart.md](../../docs/reference/chart.md) tabulates them.
`ci/full-values.yaml` turns every optional feature on for `helm lint -f` /
`helm template -f` / `kubeconform` (`make helm-template`).

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
- An S3 Secret (`s3.existingSecret`) with a `credentials` key in AWS ini
  format plus `S3_BUCKET` (and `S3_ENDPOINT` unless real AWS) — or
  `devStack.rustfs.enabled=true`, which renders one. Pods mount the file;
  nothing is injected with `envFrom`.
- `reconciler.image` and `viewer.image` must be **digest-pinned**
  (`…@sha256:…`). A tag is refused unless `devStack.allowTagImages=true`
  (PoC iteration only; tags are then pulled on every rollout).

## Installing and replaying the PoC

The install commands — the production-shaped install, the hardening steps,
and the bare-k3s replay with the devStack components — live in one place:
[docs/getting-started/deploy.md](../../docs/getting-started/deploy.md), with
the day-to-day loop in
[docs/development/local-k3s.md](../../docs/development/local-k3s.md).
Cluster-local constants come from the repo-root `.env` (`.env.example` has
the PoC defaults). The images to pin: `make poc-push` prints the wrapper and
reconciler digests; `make viewer-image` builds the viewer and `docker push
127.0.0.1:30500/uv4:<tag>` prints its digest — that is the value for
`viewer.image` (not any pre-2026-08 `uv4:v*` tag, which listens on port 80
and predates the campaign browser).

## Upgrading

Always `helm upgrade --reset-then-reuse-values` (or pass a full values
file). Plain `--reuse-values` keeps the *old* chart's defaults, which once
rendered every NetworkPolicy away; the chart now fails loudly when
`.Values.network` is missing.

### From 0.1.0 to 0.2.0 — what to decide first

| Change | What to do |
|---|---|
| **Digest gate.** `reconciler.image` / `viewer.image` must be `@sha256:` pins. | Pin the digests (above), or `--set devStack.allowTagImages=true` for the PoC loop only. |
| **Model-cache PVC is rendered** (`modelCache.create=true`, default). Helm refuses to take over a PVC it did not create. | Either `--set modelCache.create=false`, or adopt the existing PVC once (below) — adoption is the better end state (`resource-policy: keep` protects it). |
| **`image.*` and `s3.endpoint` removed.** | Drop them from your values; the schema rejects unknown keys. Campaign Jobs pin their image in the pipeline YAML; pods read the endpoint from the Secret. |
| **Namespace default deny** (`network.defaultDeny=true`). | Anything hand-applied in the namespace (a git daemon, probes) needs its own NetworkPolicy — or replace it with the chart's `devStack.gitDaemon` and drop `network.reconciler.extraEgress`. |
| **git daemon in devStack** (`devStack.gitDaemon.enabled`). | Delete the hand-applied `deploy/git-daemon` + `svc/git-daemon` (same names, same seed URL) or adopt them. |
| **RustFS console off by default**; `devStack.rustfs.nodePortConsole` → `devStack.rustfs.console.{enabled,nodePort}`. | `--set devStack.rustfs.console.enabled=true` if still wanted. |
| **RustFS credentials generated** (no more `rustfsadmin`); existing ones are re-read from the Secret on upgrade. | Nothing; read them back as shown below. |
| **Bucket policy split** by the `rustfs-init` hook: `<pipeline>/<volume>/*`, `sources/*`, `status/status.json` anonymous; `status/attempts.json`, `validation.json`, `failures/*`, `warmup/*` credentialed; `status/logs/*` anonymous while `devStack.rustfs.publicLogs=true`. | Nothing for the PoC; set `publicLogs=false` once the log viewer sits behind auth. |
| **`security.psaEnforce`** (default `baseline`, because the git daemon runs as root). | `make psa-labels` after the upgrade; `restricted` once the daemon has a purpose-built image. |
| **Registry runs as uid 1000** with a read-only rootfs. | `chown -R 1000:1000` the `registry-data` PVC from a throwaway pod, or `--set devStack.registry.runAsUser=0`. |
| **`queue.resources` default** now admits the Job the reconciler builds (cpu 4 / 8Gi / 1 GPU). | Raise it to run Jobs in parallel. |
| **Reconciler CronJob**: `startingDeadlineSeconds: 120`, `activeDeadlineSeconds` = `reconciler.tickDeadlineSeconds` (600), Lease RBAC, the `RECONCILER_*` contract env. | Requires the A1 reconciler image or newer (dulwich, Lease). |

## Immutability warning

Pipeline ConfigMaps (`templates/pipelines.yaml`, one per key under
`.Values.pipelines`) are rendered with `immutable: true`. **Never reuse a
pipeline id with different content** — bump the id instead (D17). Changing
the content under an existing id will fail to apply against a live cluster
once the ConfigMap exists, and would silently desync already-submitted Jobs
that reference the old content if it somehow did apply.

## Adopting hand-applied resources

Helm refuses to manage an object it did not create. On a cluster where the
model-cache PVC, the `nvidia` RuntimeClass, the kube-system device-plugin
DaemonSet or a `git-daemon` Deployment were applied by hand, either keep them
outside the chart (`modelCache.create=false`,
`devStack.nvidiaDevicePlugin.enabled=false`, `devStack.gitDaemon.enabled=false`)
or adopt them once before enabling the corresponding value:

```bash
kubectl -n htr-batch annotate pvc htr-test-data \
  meta.helm.sh/release-name=htr meta.helm.sh/release-namespace=htr-batch --overwrite
kubectl -n htr-batch label pvc htr-test-data app.kubernetes.io/managed-by=Helm --overwrite
# same three commands for: runtimeclass nvidia; -n kube-system daemonset nvidia-device-plugin;
# -n htr-batch deployment git-daemon + service git-daemon
```

## devStack RustFS: credentials, buckets, policy (D19)

- **Credentials** — `devStack.rustfs.{accessKey,secretKey}`, or generated
  once (32 random chars) and re-read from the existing Secret on every
  upgrade. Read them back with
  `kubectl -n htr-batch get secret htr-batch-s3 -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' | base64 -d`.
  `helm template` cannot look anything up and renders fresh random values.
- **Console** — off by default (`RUSTFS_CONSOLE_ENABLE=false`, no NodePort);
  `devStack.rustfs.console.enabled=true` exposes it on `console.nodePort`.
- **Buckets** — the `rustfs-init` Helm hook Job (post-install/upgrade,
  `devStack.rustfs.init`) creates `s3.bucket` (and `devStack.gitDaemon.bucket`,
  credentials-only), applies the bucket policy and CORS, idempotently. It
  replaces the hand-run `amazon/aws-cli` pod of the first PoC.
- **Anonymous read is split** (audit X14): `<pipeline>/<volume>/*`,
  `sources/*` and `status/status.json` are always anonymous (the viewer
  fetches them directly); `status/attempts.json`, `status/validation.json`,
  `status/failures/*` and `status/warmup/*` always need credentials;
  `status/logs/*` is anonymous only while `devStack.rustfs.publicLogs=true`
  (default — the campaign browser links run logs; they can carry a tokenised
  private IIIF URL on failure, so set it false behind an authenticated
  proxy). The policy is a single `Allow` with `NotResource` because RustFS
  applies a `Deny` to the root credential too and ignores anonymous-only
  conditions (verified 2026-08-26); `scripts/compose_init.py` renders the
  same shape for the compose stack. Listing stays denied.

## Changelog

### 0.2.0 — 2026-08-26 (audit remediation, work package A3)

Breaking:
- `image.*` and `s3.endpoint` removed (dead values; the pods take the
  endpoint from the Secret, campaign Jobs pin their image in the pipeline).
- `reconciler.image` / `viewer.image` must be digest-pinned unless
  `devStack.allowTagImages=true`. Tag refs get `imagePullPolicy: Always`.
- `devStack.rustfs.nodePortConsole` → `devStack.rustfs.console.{enabled,nodePort}`;
  the console is off by default. RustFS credentials are no longer
  `rustfsadmin` — see above.
- `queue.resources` defaults now admit the Job the reconciler builds
  (cpu 4 / memory 8Gi / nvidia.com/gpu 1).
- `values.schema.json`: unknown keys are rejected; `.Values.network` is required.
- Namespace-wide default-deny NetworkPolicy (`network.defaultDeny`, on):
  hand-applied pods in the namespace need their own policy.

Added:
- `modelCache.{create,name,size,storageClass,accessModes}` renders the model
  cache PVC (kept on uninstall) and feeds `RECONCILER_DATA_PVC`.
- Reconciler CronJob: `startingDeadlineSeconds: 120`, `activeDeadlineSeconds`
  from `reconciler.tickDeadlineSeconds` (600), Lease RBAC
  (`coordination.k8s.io/leases`), env `RECONCILER_TICK_SECONDS`,
  `RECONCILER_TICK_DEADLINE_SECONDS`, `RECONCILER_GIT_TIMEOUT`, optional
  `GIT_TOKEN` (`reconciler.gitTokenSecret`),
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
  `publicResultsBase`) so the SPA finds `status.json` under its CSP, pod
  rolls on config change.
- devStack: RustFS/registry restricted securityContext (RustFS uid 10001,
  registry `devStack.registry.runAsUser`), digest-pinned images, bucket-init
  hook Job, `devStack.gitDaemon.*` (seeded from the bucket, own NetworkPolicy),
  `helm.sh/resource-policy: keep` on the S3 Secret, PVCs and the registry
  Namespace, sizing notes.
- ClusterQueue `namespaceSelector` limited to the release namespace;
  `network.reconciler.egressCidrs` narrowed to GitHub's git ranges;
  `network.viewer.ingressCidrs`.

### 0.1.0

Initial chart (queues, pipelines, viewer, devStack, reconciler, NetworkPolicies).
