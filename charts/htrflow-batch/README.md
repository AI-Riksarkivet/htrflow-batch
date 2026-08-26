# htrflow-batch (Helm chart)

Kueue-gated batch HTR platform around the htrflow image: queues, pipeline
ConfigMaps, the model-cache PVC, the GitOps reconciler, NetworkPolicies and a
results viewer. Per-volume Jobs are submitted at runtime by the reconciler,
not by this chart — `templates/job-example.yaml` is a smoke artifact for PoC
replay only (`exampleJob.enabled`, off by default).

Every value is declared in `values.schema.json` (unknown keys and wrong types
are rejected); `values.yaml` documents each one. `ci/full-values.yaml` turns
every optional feature on for `helm lint -f` / `helm template -f` /
`kubeconform` (`make helm-template`).

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
  `devStack.rustfs.enabled=true`, which renders one.
- `reconciler.image` and `viewer.image` must be **digest-pinned**
  (`…@sha256:…`). A tag is refused unless `devStack.allowTagImages=true`
  (PoC iteration only; tags are then pulled on every rollout).

## Upgrading

Always `helm upgrade --reset-then-reuse-values` (or pass a full values
file). Plain `--reuse-values` keeps the *old* chart's defaults, which once
rendered every NetworkPolicy away; the chart now fails loudly when
`.Values.network` is missing.

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

## PoC replay (bare k3s, in-cluster devStack)

This reproduces the smoke test (see the
[development test log](../../docs/development/test-log.md)) using the
chart's optional `devStack.*` components (RustFS S3 with its bucket-init
Job, an in-cluster registry, the NVIDIA device plugin, a git daemon) instead
of the standalone raw manifests they replace. Cluster-local constants come
from the repo-root `.env` (`.env.example` has the defaults used below).

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
make poc-push          # prints the wrapper/reconciler digests to pin below
helm upgrade --install htr charts/htrflow-batch -n htr-batch --create-namespace \
  --set devStack.rustfs.enabled=true --set devStack.registry.enabled=true \
  --set devStack.nvidiaDevicePlugin.enabled=true --set devStack.gitDaemon.enabled=true \
  --set devStack.allowTagImages=true \
  --set publicResultsBase=http://localhost:30900/htr-results \
  --set network.apiServer.cidr=<node-ip>/32 \
  --set viewer.image=127.0.0.1:30500/uv4:dev \
  --set reconciler.enabled=true --set reconciler.image=127.0.0.1:30500/htrflow-reconciler:dev \
  --set reconciler.campaignsRepoUrl=git://git-daemon.htr-batch.svc.cluster.local/campaigns-local \
  --set exampleJob.enabled=true --set exampleJob.image=127.0.0.1:30500/htrflow-batch:dev \
  --set exampleJob.manifestUrl=http://rustfs.htr-batch.svc.cluster.local:9000/htr-fixtures/mock-vol/manifest.json \
  --set-file pipelines.demo-v1=.docker/pipeline-demo-v1.yaml
make psa-labels
make warmup PIPELINE=demo-v1 IMAGE=127.0.0.1:30500/htrflow-batch:dev
kubectl -n htr-batch patch job htr-vol-301 --type=json -p '[{"op":"replace","path":"/spec/suspend","value":false}]'
k9s -n htr-batch   # watch
```

`exampleJob.enabled=true` renders the `htr-vol-301` Job with `suspend: true`;
unsuspend it (or `kubectl create job --from`) to run the smoke test.
Kill-and-resume test: wait until ~2 ALTOs exist under
`demo-v1/mock-vol/alto/`, force-delete the pod, watch the retry pod log
`resume: N pages already done` and converge to Complete.

Host prerequisites (persisted on the PoC node): `fs.inotify.max_user_instances=1024`
+ `max_user_watches=1048576` (`/etc/sysctl.d/99-k3s-inotify.conf`), k3s
`node-ip` pinned in `/etc/rancher/k3s/config.yaml` (hostname resolves
IPv6-only).

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
  `RECONCILER_MAX_VALIDATIONS_PER_TICK`, `RECONCILER_FETCH_MAX_BYTES`,
  `RECONCILER_LEASE_NAME`, `RECONCILER_JOB_{MIN_DEADLINE_SECONDS,SECONDS_PER_PAGE,RUNTIME_CLASS,NODE_SELECTOR,TOLERATIONS}`,
  `RECONCILER_JOB_{MANIFEST_MAX_BYTES,FETCH_MAX_BYTES}`,
  `RECONCILER_ALLOWED_IMAGE_REPOS`, `RECONCILER_REQUIRE_MODEL_REVISION`.
- `job.{runtimeClassName,nodeSelector,tolerations,minDeadlineSeconds,secondsPerPage,manifestMaxBytes,fetchMaxBytes}`;
  the example Job mirrors them plus `backoffLimit: 0` + `podFailurePolicy`.
- `security.{allowedImageRepos,requireModelRevision,psaEnforce,verifyImages.*}`;
  optional Kyverno `ClusterPolicy` (cosign keyless) when `verifyImages.enabled`.
- Viewer: restricted securityContext (uid 101, read-only rootfs, no SA
  token), nginx security headers (`viewer.securityHeaders.enabled`), pod
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
