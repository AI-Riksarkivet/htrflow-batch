# Deploy

Everything ships as a single helm chart, `charts/htrflow-batch` (0.2.0).
Per-volume Jobs are submitted at runtime by the reconciler (see
[Running a Campaign](campaigns.md)) — the chart deploys the queueing, the
model-cache PVC, the viewer, the reconciler, the NetworkPolicies and
(optionally) the PoC support infrastructure. Every value is in
[Chart Values](../reference/chart.md).

## Production-shaped install

```bash
helm install htr charts/htrflow-batch -n htr-batch --create-namespace \
  --set publicResultsBase=<browser-reachable results base URL> \
  --set viewer.image=<registry>/htrflow-batch-viewer@sha256:<digest> \
  --set network.s3Cidrs='{<s3 endpoint cidr>}' \
  --set security.allowedImageRepos='{<registry>/}'
make psa-labels
```

Kueue CRDs must already be installed on the cluster — see
[Prerequisites](index.md). The chart does not install the Kueue controller.
`s3.existingSecret` (default `htr-batch-s3`) must already exist with a
`credentials` key in AWS ini format — pods read it as a mounted file, never
as env ([Security](../development/security.md)) — plus `S3_BUCKET` and, for
anything but real AWS, `S3_ENDPOINT`:

```ini
[default]
aws_access_key_id = …
aws_secret_access_key = …
```

The bucket needs anonymous `GetObject` on `<pipeline>/<volume>/*`,
`sources/*` and `status/status.json` plus CORS (GET/HEAD from the viewer
origin) — the browser fetches manifests, ALTO and `status.json` directly.
The devStack `rustfs-init` hook applies exactly that policy to RustFS
(`templates/_helpers.tpl`, `bucketPolicy`); a real bucket needs the
equivalent ([Security → The bucket policy](../development/security.md#the-bucket-policy)).

`viewer.image` must be a digest (the chart refuses tags outside the PoC);
build it with `make viewer-image` / `dagger call build-viewer` — the
published `:latest` predates the campaign browser.

Queue quota is a plain list of covered resources under `queue.resources`.
The default admits exactly one Job as the reconciler builds it (requests
cpu 4 / 8 Gi / 1 GPU); raise the quotas to run Jobs in parallel:

```yaml
queue:
  resources:
    - name: cpu
      quota: 8
    - name: memory
      quota: 16Gi
    - name: nvidia.com/gpu
      quota: 2
```

The model cache PVC (`modelCache.*`, default 30 Gi RWO, kept on uninstall)
is rendered by the chart. Pipeline ConfigMaps under `.Values.pipelines` are
rendered immutable, one per id — never reuse a pipeline id with different
content, bump the id instead; campaign pipelines come from git and are
managed by the reconciler.

## Hardening steps the chart cannot do alone

- **Namespace labels** (once): `make psa-labels` — Pod Security Admission
  `enforce=<security.psaEnforce>` (`baseline` by default, only because the
  devStack git daemon runs as root; `restricted` otherwise),
  `warn=restricted`, `audit=restricted`.
- **NetworkPolicy inputs** (`network.*` values): the IIIF origin CIDR(s)
  (`network.iiifCidrs`), a real S3 endpoint (`network.s3Cidrs`) when not
  using devStack RustFS, and the campaigns host for the reconciler
  (`network.reconciler.egressCidrs`, GitHub's `git` ranges by default). Node
  addresses and the apiserver endpoint are looked up at install time; set
  `network.nodeCidrs` / `network.apiServer.cidr` when rendering with
  `helm template` or without list-nodes permission.
- **Trust boundary** (`security.*`): set `security.allowedImageRepos` — an
  empty list lets any digest-pinned image in the campaigns repo run on the
  GPU (the reconciler warns) — and consider `requireModelRevision: true` and
  the Kyverno `verifyImages` policy once images are cosign-signed
  ([Security → Trust boundary](../development/security.md#trust-boundary)).
- **Model cache**: Jobs run offline on a read-only cache. Pipelines from the
  campaigns repo are warmed by the reconciler; chart-declared ones with
  `make warmup PIPELINE=<id> IMAGE=<ref>`. A cache PVC that predates the
  non-root pods needs a one-time `chown -R 1000:1000`
  ([Security](../development/security.md#cache-pvc-migration)).
- **Campaigns repo**: protected `main`, required review — write access to it
  is code execution on the GPU node ([Running a Campaign](campaigns.md)).

## Upgrading

```bash
helm upgrade htr charts/htrflow-batch -n htr-batch --reset-then-reuse-values [--set …]
make psa-labels
```

Always `--reset-then-reuse-values` (or a full values file): plain
`--reuse-values` keeps the old chart's defaults and once rendered every
NetworkPolicy away. The 0.1.0 → 0.2.0 decisions (digest gate, PVC
adoption, removed `image.*`/`s3.endpoint`, default deny, git daemon, console
off, bucket-policy split, `psaEnforce`) are tabulated in the
[chart README](https://github.com/carpelan/test/blob/main/charts/htrflow-batch/README.md#upgrading).

## PoC replay (bare k3s, in-cluster devStack)

This reproduces the smoke test on a single k3s node using the chart's
optional `devStack.*` components (RustFS S3 with its bucket-init hook, an
in-cluster registry, the NVIDIA device plugin and RuntimeClass, a git daemon
serving the campaigns repo) instead of standalone raw manifests. Cluster
constants come from the repo-root `.env` (`.env.example` has the defaults
used here); the day-to-day loop around this — building the arm64 GPU image,
seeding the git daemon, SSH forwards — is
[Local k3s development](../development/local-k3s.md).

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
make poc-push                       # builds + pushes wrapper and reconciler, prints their digests
make viewer-image && docker push 127.0.0.1:30500/uv4:dev   # prints the viewer digest
helm upgrade --install htr charts/htrflow-batch -n htr-batch --create-namespace \
  --set devStack.rustfs.enabled=true --set devStack.registry.enabled=true \
  --set devStack.nvidiaDevicePlugin.enabled=true --set devStack.gitDaemon.enabled=true \
  --set publicResultsBase=http://localhost:30900/htr-results \
  --set network.apiServer.cidr=<node-ip>/32 \
  --set viewer.image=127.0.0.1:30500/uv4@sha256:<viewer digest> \
  --set reconciler.enabled=true \
  --set reconciler.image=127.0.0.1:30500/htrflow-reconciler@sha256:<reconciler digest> \
  --set reconciler.campaignsRepoUrl=git://git-daemon.htr-batch.svc.cluster.local/campaigns-local \
  --set security.allowedImageRepos='{127.0.0.1:30500/}' \
  --set exampleJob.enabled=true --set exampleJob.image=127.0.0.1:30500/htrflow-batch:dev \
  --set exampleJob.manifestUrl=http://rustfs.htr-batch.svc.cluster.local:9000/htr-fixtures/mock-vol/manifest.json \
  --set-file pipelines.demo-v1=.docker/pipeline-demo-v1.yaml
make psa-labels
make warmup PIPELINE=demo-v1 IMAGE=127.0.0.1:30500/htrflow-batch:dev
kubectl -n htr-batch patch job htr-vol-301 --type=json -p '[{"op":"replace","path":"/spec/suspend","value":false}]'
k9s -n htr-batch   # watch
```

`--set devStack.allowTagImages=true` lets you use `:dev` tags for
`reconciler.image` / `viewer.image` instead of the digests while iterating
(they are then pulled on every rollout). Swap `helm upgrade --install` for
`helm template` (same flags, plus `network.nodeCidrs`) to render without a
cluster. `exampleJob.enabled=true` renders the `htr-vol-301` Job with
`suspend: true`; unsuspend it (or `kubectl create job --from`) to run the
smoke test. Kill-and-resume test: wait until ~2 ALTOs exist under
`demo-v1/mock-vol/alto/`, force-delete the pod, watch the retry pod log
`resume: N pages already done` and converge to Complete.

On a cluster that already carries a hand-made model-cache PVC, RuntimeClass,
device plugin or git daemon, adopt them first or turn the corresponding
value off (chart README, "Adopting hand-applied resources").

Bucket policy and CORS are applied by the `rustfs-init` hook on every
install/upgrade — no manual `aws-cli` pod any more. The RustFS credentials
are generated on first install; read them back with
`kubectl -n htr-batch get secret htr-batch-s3 -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' | base64 -d`.

Host prerequisites (persisted on the PoC node) are in
[Prerequisites](index.md#bare-k3s-poc-path-host-gotchas).

## Local compose smoke stack

No Kubernetes cluster needed — see [Run a Volume](run-a-volume.md#local-compose-alternative)
for the `make compose-up` / `make compose-smoke` path.
