# Deploy

Everything runtime-facing ships as two Helm charts. `charts/htrflow-batch`
(0.4.0) deploys the queueing (Kueue objects), the model-cache PVC, the web
front (campaign browser, Universal Viewer and the read-only status API in
one Deployment) and the NetworkPolicies.
`charts/htrflow-devstack` is the separate, PoC-only chart for in-cluster
RustFS/registry/NVIDIA-device-plugin stand-ins ([Local k3s
development](../development/local-k3s.md)). Campaigns themselves are not
part of either chart — they are Indexed Jobs rendered by `packages/converter`
from a campaigns repo and applied with `kubectl` or Argo CD (see [Running a
Campaign](campaigns.md)). Every chart value is in
[Chart Values](../reference/chart.md).

## Production-shaped install

```bash
helm install htr charts/htrflow-batch -n htr-batch --create-namespace \
  --set publicResultsBase=<browser-reachable results base URL> \
  --set web.image=<registry>/htrflow-web@sha256:<digest> \
  --set network.s3Cidrs='{<s3 endpoint cidr>}' \
  --set network.apiServer.cidr=<kube-apiserver cidr, e.g. 10.16.51.56/32> \
  --set security.allowedImageRepos='{<registry>/}' \
  --set security.policies.enabled=true
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

The bucket needs anonymous `GetObject` on `<namespace>/<pipeline>/<volume>/*`,
`sources/*` and `status/logs/*` plus CORS (GET/HEAD from the web front's origin)
— the browser fetches manifests, ALTO and the live run log directly, and
`GET /api/v1/jobs` for everything else. The devStack `rustfs-init` hook
applies exactly that policy to RustFS (`templates/_helpers.tpl`,
`bucketPolicy`); a real bucket needs the equivalent
([Security → The bucket policy](../development/security.md#the-bucket-policy)).

`web.image` must be a digest (the chart refuses tags outside the PoC —
`security.allowTagImages`); build it with `make build-web` / `dagger call
build-web`, publish it with `dagger call publish-docker --component web`.
One image carries the read API, the campaign browser and the Universal
Viewer, and it takes the NodePort (`web.nodePort`, default 30800).

Queue quota is a plain list of covered resources under `queue.resources`.
The default admits exactly one campaign index as the converter renders it
(requests cpu 4 / 8 Gi / 1 GPU); raise the quotas to run more volumes in
parallel:

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
is rendered by the chart; the converter's rendered warm-up Jobs and campaign
Jobs are what actually write to and read from it — nothing in the chart
renders a pipeline ConfigMap or a Job any more.

## Hardening steps the chart cannot do alone

- **Namespace labels** (once): `make psa-labels` — Pod Security Admission
  `enforce=<security.psaEnforce>` (`baseline` by default; nothing left in
  either chart actually needs it — `restricted` is worth trying now),
  `warn=restricted`, `audit=restricted`.
- **NetworkPolicy inputs** (`network.*` values): the IIIF origin CIDR(s)
  (`network.iiifCidrs`), a real S3 endpoint (`network.s3Cidrs`) when not
  using devStack RustFS, and `network.apiServer.cidr` for the read API's
  egress to the kube-apiserver (auto-detected via Helm `lookup` at install
  time; set it explicitly for `helm template` or a kubeconfig without
  list-nodes permission).
- **Trust boundary** (`security.*`): set `security.allowedImageRepos` and
  turn on `security.policies.enabled` — the Kyverno ClusterPolicies are the
  only thing enforcing the allow-list and the model-revision rule since the
  converter dropped both, and an empty list lets any image run on the GPU.
  Kyverno must be installed first (`make install-kyverno`; ai-dev story
  I04). Consider `security.requireModelRevision: true` and the
  `verifyImages` policy once images are cosign-signed
  ([Security → Trust boundary](../development/security.md#trust-boundary)).
- **Model cache**: campaign and warm-up Jobs run offline on a read-only /
  writable-by-warm-up-only cache respectively. A cache PVC that predates the
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
NetworkPolicy away. Chart history (digest gate, PVC adoption, the 0.3.0
removal of the old CronJob controller/pipelines/exampleJob, the read API's
arrival, the `devStack.*` split into `charts/htrflow-devstack`) is tabulated
in the
[chart README](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/charts/htrflow-batch/README.md#upgrading).

## PoC replay (bare k3s, in-cluster devStack)

This reproduces the smoke test on a single k3s node using
`charts/htrflow-devstack` (RustFS S3 with its bucket-init hook, an
in-cluster registry, the NVIDIA device plugin and RuntimeClass) instead of
standalone raw manifests. Cluster constants come from the repo-root `.env`
(`.env.example` has the defaults used here); the day-to-day loop around this
— building the arm64 GPU image, SSH forwards — is
[Local k3s development](../development/local-k3s.md).

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
make poc-push                       # builds + pushes the wrapper and web images, prints their digests
helm upgrade --install htr-devstack charts/htrflow-devstack -n htr-batch --create-namespace \
  --set rustfs.enabled=true --set registry.enabled=true \
  --set nvidiaDevicePlugin.enabled=true
helm upgrade --install htr charts/htrflow-batch -n htr-batch \
  --set publicResultsBase=http://localhost:30900/htr-results \
  --set network.apiServer.cidr=<node-ip>/32 \
  --set web.image=127.0.0.1:30500/htrflow-web@sha256:<web digest> \
  --set security.allowedImageRepos='{127.0.0.1:30500/}' \
  --set security.policies.enabled=true    # needs `make install-kyverno`
make psa-labels
make campaigns-apply DIR=examples/campaigns   # or your own campaigns repo checkout
k9s -n htr-batch   # watch
```

`--set security.allowTagImages=true` lets you use a `:dev` tag for
`web.image` instead of a digest while iterating (it is then pulled on every
rollout). Swap `helm upgrade --install` for `helm
template` (same flags, plus `network.nodeCidrs`) to render without a
cluster. Kill-and-resume test: wait until ~2 ALTOs exist under
`<namespace>/demo-v1/mock-vol/alto/`, force-delete the running pod, watch
the retry pod's log `resume: N pages already done` and converge to
`Complete`.

On a cluster that already carries a hand-made model-cache PVC, RuntimeClass
or device plugin, adopt it first or turn the corresponding value off (chart
README, "Adopting hand-applied resources").

Bucket policy and CORS are applied by the `rustfs-init` hook on every
install/upgrade — no manual `aws-cli` pod any more. The RustFS credentials
are generated on first install; read them back with
`kubectl -n htr-batch get secret htr-batch-s3 -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' | base64 -d`.

Host prerequisites (persisted on the PoC node) are in
[Prerequisites](index.md#bare-k3s-poc-path-host-gotchas).

## Local compose smoke stack

No Kubernetes cluster needed — see [Run a Volume](run-a-volume.md#local-compose-alternative)
for the `make compose-up` / `make compose-smoke` path.
