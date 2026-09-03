# Local k3s development

The day-to-day loop on the single-node GPU PoC: the GB10 (arm64, one GPU)
running k3s, with the platform installed as release `htr` in namespace
`htr-batch` and every dependency provided by `charts/htrflow-devstack` —
RustFS S3 (NodePort 30900), an image registry (namespace `registry`,
NodePort 30500), and the NVIDIA RuntimeClass + device plugin. Kueue itself
is installed separately (`kueue-system`); neither chart renders its
controller, only `charts/htrflow-batch`'s queue objects. The install
command is in [Deploy → PoC replay](../getting-started/deploy.md#poc-replay-bare-k3s-in-cluster-devstack);
this page is everything around it.

## `.env`

Cluster-local constants for the Makefile and the compose stack live in a
git-ignored `.env` at the repo root; `.env.example` carries the PoC
defaults and is loaded first, so a missing `.env` behaves exactly like it:

| Key | Default | Used by |
|---|---|---|
| `HTR_RELEASE` / `HTR_NAMESPACE` | `htr` / `htr-batch` | `helm-template`, `psa-labels` |
| `HTR_REGISTRY` / `HTR_REGISTRY_NODEPORT` | `127.0.0.1:30500` / `30500` | `poc-push` (image names) |
| `HTR_S3_ENDPOINT` / `HTR_S3_NODEPORT` / `HTR_BUCKET` | `http://localhost:30900` / `30900` / `htr-results` | compose; your own `aws` calls |
| `HTR_WEB_NODEPORT` | `30800` | `make e2e`'s final `/api/v1/jobs` curl |
| `HTRFLOW_DIR` | `~/htrflow` | `build-htrflow-base-arm64`; the base's `git describe` |
| `HTR_DEV_S3_ACCESS_KEY` / `SECRET_KEY` | `rustfsadmin` | the compose stack only — never a cluster |

The chart takes the same values via `--set`; the Python packages take them
from their own env ([Reference](../reference/index.md)).

## The arm64 GPU wrapper image

The upstream `airiksarkivet/htrflow` image is amd64-only. It *runs* on the
GB10 under qemu (binfmt), but the GPU never crosses the emulation boundary
(a 2-page volume: ~55 s native vs 1 h+ emulated on CPU) and the `uv` binary
segfaults under `qemu-x86_64`. So the wrapper is built natively, from the
one `.docker/htrflow-batch.dockerfile` — its `base-arm64` stage, which
`docker build` selects from the host's `TARGETARCH` — on top of a locally
built base:

```bash
# 0. htrflow's lockfile is gitignored — create it in that checkout yourself
#    (this repo treats ~/htrflow as read-only and the target below refuses
#    to run without a uv.lock rather than writing one into someone else's
#    working tree):
(cd "$HTRFLOW_DIR" && uv lock)
# 1. the base, from the htrflow checkout (HTRFLOW_DIR)
make build-htrflow-base-arm64                # docker/htrflow.dockerfile -> htrflow:v0.2.6-arm64

# 2. the wrapper + web front, pushed to the in-cluster registry
make poc-push                                # builds the host's architecture
```

CI does exactly this on an `ubuntu-24.04-arm` runner, in a throwaway clone
of htrflow pinned to `HTRFLOW_ARM64_BASE_REF`
(`.github/actions/build-htrflow-base-arm64`), so the *recipe* on this page
and the one that publishes images cannot drift. The *resolved dependency
set* can: htrflow's `uv.lock` is gitignored, so the pin fixes htrflow's
source, not what `uv lock` resolves on the day — the base CI builds is not
bit-identical to the node's. The explicit `torch==2.13.0` /
`torchvision==0.28.0` pins in the wrapper dockerfile are what turn a drift
in the base's resolution into a build failure instead of a silent change.

`poc-push` stamps `HTRFLOW_BASE_REVISION` (`git -C $HTRFLOW_DIR describe
--tags --always --dirty`) into the image label
`se.riksarkivet.htrflow.base.revision`, because `manifest.json` only knows
the package version (`0.2.6`) while the base is built well past that tag.
It prints the wrapper and API digests at the end — the wrapper's goes into
`pipelines/<id>.yaml` in the campaigns repo, the API's into `web.image`
(or use `security.allowTagImages=true` and the `:dev` tag while iterating;
tags are then pulled on every rollout).

Three extras beyond the amd64 branch are required and pinned in the
dockerfile, all behind `if [ "$TARGETARCH" = "arm64" ]`: `gcc` + Python
headers (triton's JIT needs a compiler at runtime), `sentencepiece`, and
`transformers<5` (v5 dropped the slow→fast tokenizer conversion; models
without `tokenizer.json`, e.g. `microsoft/trocr-base-handwritten`, fail).
torch comes from the base's own lock (PyPI aarch64 wheels bundle CUDA 13;
torch reports `2.13.0+cu130` and runs on the GB10) — the "cu128 swap" of
the amd64 branch is a no-op on this arch and the dockerfile pins the
versions explicitly so a drifting base fails the build instead of silently
changing them.

containerd pulls `127.0.0.1:30500/…` over plain HTTP with no
`registries.yaml` (localhost fallback); `docker push` to it from the node
also just works. The web image needs nothing checked out beside it —
`make build-web` builds the SPA and the Universal Viewer inside the image —
so `make poc-push` covers both images and prints the digest for
`web.image`.

## The campaigns repo, applied from a laptop

Pipeline files pin `127.0.0.1:30500/…` digests, which resolve only on this
node — so the PoC's campaigns repo must never reach a shared `main` (and
there is nothing in-cluster that needs to clone it any more: no CronJob
controller, no git daemon). Render and apply directly against the PoC
cluster with `kubectl`:

```bash
cd ~/htr-test                                   # the PoC campaigns repo, branch local-k3s
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
make -C ~/htrflow-batch campaigns-apply DIR=$PWD
```

which renders `rendered/pipelines` and `rendered/campaigns` and applies
them (pipelines first). Repeat after every commit to the PoC branch — there
is no polling loop watching it for you. See [Campaign & Pipeline
YAML](../reference/campaign-yaml.md) for the append-only rule this hits if
you try to edit an already-rendered campaign's volume list in place.

## Two S3 endpoints, one results base

`publicResultsBase=http://localhost:30900/htr-results` is what a browser on
your laptop reaches through the SSH forward — but the wrapper pod itself
never resolves `localhost`, so its own S3 writes go through `S3_ENDPOINT`
(the in-cluster RustFS Service address, from the S3 Secret), while
`PUBLIC_RESULTS_BASE` is only ever embedded as browser-facing text
(`viewer_url` in `manifest.json`, the `id` inside a published `iiif.json`
or synthetic manifest) — there is no separate "internal results base" to
keep in sync any more, and nothing rewrites URLs after the fact (there is
no `status.json` left to do that job). On real AWS (`S3_ENDPOINT` empty)
this distinction disappears entirely. Anything *you* put in a campaign
file — a fixture manifest on the RustFS bucket, say — must use the
in-cluster form (`http://rustfs.htr-batch.svc.cluster.local:9000/…`),
because the pod, not your browser, fetches it.

## Reaching it from a laptop

The node's address is not routable from a laptop, so forward both NodePorts
through a host that can reach it:

```bash
ssh -L 30800:<node-ip>:30800 -L 30900:<node-ip>:30900 <ssh-host>
```

Then `http://localhost:30800/` is the campaign browser, the viewer and the
read API, and `http://localhost:30900/htr-results/…` is what every link on
it resolves to. Both are required — the page and `/api/v1/…` come from
30800, results, logs, images and ALTO from 30900. This is exactly why
`publicResultsBase` is a `localhost` URL on the PoC
([Viewing Results](../getting-started/viewing.md#the-localhost-url-caveat)).
`kubectl port-forward` on the registry Service is the equivalent for
pushing images from a laptop.

## Hand-made resources the charts now render

The first PoC applied the model-cache PVC, the `nvidia` RuntimeClass and the
kube-system device-plugin DaemonSet by hand. Helm refuses to take over
objects it did not create, so on this cluster each is either **adopted**
once (annotate `meta.helm.sh/release-name`/`release-namespace`, label
`app.kubernetes.io/managed-by=Helm` — commands in the chart READMEs) and
then rendered (`modelCache.create=true` in `charts/htrflow-batch`,
`nvidiaDevicePlugin.enabled=true` in `charts/htrflow-devstack`), or left
outside (`modelCache.create=false` keeps using the existing
`htr-test-data`; the device plugin off). Adoption is the better end state:
the PVC gets `resource-policy: keep`, and the device-plugin image is
digest-pinned by the chart. The cache PVC was `chown -R 1000:1000`'d once
when the pods went non-root ([Security](security.md#cache-pvc-migration)).

## Upgrading the live release

```bash
helm upgrade htr charts/htrflow-batch -n htr-batch --reset-then-reuse-values \
  --set web.image=127.0.0.1:30500/htrflow-web@sha256:<digest>
make psa-labels
```

The same applies to `charts/htrflow-devstack` (e.g. a new default such as
`registry.fixOwnership` never reaches an existing release with plain
`--reuse-values`). Always `--reset-then-reuse-values`: plain `--reuse-values` keeps the old
chart's defaults and once rendered every NetworkPolicy away. Chart history
for this cluster (PVC adoption, registry uid, console, `allowTagImages`,
the 0.3.0 `devStack.*` split into `charts/htrflow-devstack`) is tabulated
in the chart READMEs.

## Gotchas collected on this node

- **`helm template` needs `network.apiServer.cidr`** (and `nodeCidrs`): the
  read API's NetworkPolicy is built from a `lookup` that only works against
  a cluster.
- **Pipelines pinned to an image that predates `htrflow_batch.warmup`**
  cannot be submitted to any more — the warm-up gate blocks on the marker
  file every campaign pod's init container waits for, so an old pipeline id
  with a stale image just never clears it; new work needs a new pipeline id
  on a current image.
- **Kueue quota is one GPU**: a second index waits Suspended until the
  first finishes (a 480-spread volume runs ~12–13 s/page). `queued` with an
  idle GPU means Kueue is down, not busy.
- **`make campaigns-apply` is safe to re-run**: `render` is a pure function
  and `kubectl apply` is idempotent, so re-running only matters when the
  campaigns repo actually changed. (Verified against running, completed and
  failed Jobs — the converter no longer uses Kueue partial admission, which
  used to rewrite `spec.parallelism` on the live Job and make the rendered
  file un-appliable: [E2E log](e2e-indexed-jobs.md).)
- **Cancelling needs `PRUNE=1`**: a plain `kubectl apply` never deletes.
- **`make install-devstack NVIDIA_DEVICE_PLUGIN=false` refuses while GPU
  pods are running**: disabling the plugin deletes the chart-managed
  `nvidia` RuntimeClass and device-plugin DaemonSet, which took a live pod
  down for two minutes the one time it happened
  ([E2E log](e2e-indexed-jobs.md), "A failed Helm install still owns what
  it applied"). The target checks the cluster for a pod outside
  `kube-system` that is Running or Pending (finished pods do not hold the
  GPU) and uses `runtimeClassName: nvidia` or requests
  `nvidia.com/gpu`, and exits non-zero if it finds one; `FORCE=1` skips the
  check.
  `make campaigns-apply DIR=… PRUNE=1` adds
  `--prune -l htrflow.riksarkivet.se/managed-by=converter`, which is what
  actually removes a deleted campaign's Job and ConfigMap. Only ever run it
  against the *whole* campaigns repo — against a partial checkout it cancels
  everything the checkout does not contain.
- **Pausing is `suspend: true` in the campaign file plus the apply step.**
  The rendered `spec.suspend` alone does not hold — Kueue owns that field for
  an admitted Workload and undoes it in seconds — so the last step of
  `htrflow-campaigns apply` patches the Workload's `spec.active`. Never
  `kubectl edit` the Job ([E2E log](e2e-indexed-jobs.md)).
- **RustFS is single-disk**: the results bucket is one `local-path` PVC on
  this node (`rustfs-data`, 5 Gi by default, kept on uninstall). Fine for
  iteration; not an archive.
- Host prerequisites from the first PoC (inotify limits, `node-ip` pin,
  absolute eviction thresholds) are in [Prerequisites](../getting-started/index.md#bare-k3s-poc-path-host-gotchas).
