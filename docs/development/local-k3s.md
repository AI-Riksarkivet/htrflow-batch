# Local k3s development

The day-to-day loop on the single-node GPU PoC: the GB10 (arm64, one GPU)
running k3s, with the platform installed as release `htr` in namespace
`htr-batch` and every dependency provided by the chart's `devStack.*`
components — RustFS S3 (NodePort 30900), an image registry (namespace
`registry`, NodePort 30500), the NVIDIA RuntimeClass + device plugin, and a
git daemon serving the campaigns repo. Kueue itself is installed separately
(`kueue-system`); the chart only renders its queue objects. The install
command is in [Deploy → PoC replay](../getting-started/deploy.md#poc-replay-bare-k3s-in-cluster-devstack);
this page is everything around it.

## `.env`

Cluster-local constants for the Makefile and the compose stack live in a
git-ignored `.env` at the repo root; `.env.example` carries the PoC
defaults and is loaded first, so a missing `.env` behaves exactly like it:

| Key | Default | Used by |
|---|---|---|
| `HTR_RELEASE` / `HTR_NAMESPACE` | `htr` / `htr-batch` | `helm-template`, `warmup`, `psa-labels` |
| `HTR_REGISTRY` / `HTR_REGISTRY_NODEPORT` | `127.0.0.1:30500` / `30500` | `poc-push`, `viewer-image` (image names) |
| `HTR_S3_ENDPOINT` / `HTR_S3_NODEPORT` / `HTR_BUCKET` | `http://localhost:30900` / `30900` / `htr-results` | compose; your own `aws` calls |
| `HTR_VIEWER_NODEPORT` | `30800` | — |
| `HTR_DATA_PVC` | `htr-test-data` | `make warmup` |
| `HTRFLOW_DIR` | `~/htrflow` | the arm64 base build's `git describe` |
| `HTR_DEV_S3_ACCESS_KEY` / `SECRET_KEY` | `rustfsadmin` | the compose stack only — never a cluster |

The chart takes the same values via `--set`; the Python packages take them
from their own env ([Reference](../reference/index.md)).

## The arm64 GPU wrapper image

The upstream `airiksarkivet/htrflow` image is amd64-only. It *runs* on the
GB10 under qemu (binfmt), but the GPU never crosses the emulation boundary
(a 2-page volume: ~55 s native vs 1 h+ emulated on CPU) and the `uv` binary
segfaults under `qemu-x86_64`. So the wrapper is built natively from
`.docker/htrflow-batch-gpu-arm64.dockerfile` on top of a locally built
base:

```bash
# 1. the base, from the htrflow checkout (HTRFLOW_DIR). The lockfile is
#    gitignored there, so lock first — a missing uv.lock fails the build.
cd ~/htrflow && uv lock && docker build -f docker/htrflow.dockerfile -t htrflow:v0.2.6-arm64 .

# 2. the wrapper + reconciler, pushed to the in-cluster registry
cd ~/htrflow-batch && make poc-push          # arch-aware: picks the arm64 recipe on aarch64
make poc-push-arm64                          # the same recipe regardless of host arch
```

`poc-push` stamps `HTRFLOW_BASE_REVISION` (`git -C $HTRFLOW_DIR describe
--tags --always --dirty`) into the image label
`se.riksarkivet.htrflow.base.revision`, because `manifest.json` only knows
the package version (`0.2.6`) while the base is built well past that tag.
It prints the wrapper and reconciler digests at the end — the wrapper's goes
into `pipelines/<id>.yaml` in the campaigns repo, the reconciler's into
`reconciler.image` (or use `devStack.allowTagImages=true` and the `:dev`
tag while iterating; tags are then pulled on every rollout).

Three extras beyond the amd64 recipe are required and pinned in the
dockerfile: `gcc` + Python headers (triton's JIT needs a compiler at
runtime), `sentencepiece`, and `transformers<5` (v5 dropped the slow→fast
tokenizer conversion; models without `tokenizer.json`, e.g.
`microsoft/trocr-base-handwritten`, fail). torch comes from the base's own
lock (PyPI aarch64 wheels bundle CUDA 13; torch reports `2.13.0+cu130` and
runs on the GB10) — the "cu128 swap" of the amd64 recipe is a no-op on this
arch and the dockerfile pins the versions explicitly so a drifting base
fails the build instead of silently changing them.

containerd pulls `127.0.0.1:30500/…` over plain HTTP with no
`registries.yaml` (localhost fallback); `docker push` to it from the node
also just works. The viewer: `make viewer-image` (needs a built
`universalviewer4` checkout at `UV4_DIR`, default `~/universalviewer4`) and
`docker push 127.0.0.1:30500/uv4:dev`, which prints the digest for
`viewer.image`.

## The campaigns repo, served in-cluster

Pipeline files pin `127.0.0.1:30500/…` digests, which resolve only on this
node — so the PoC's campaigns repo must never reach a shared `main`, and the
reconciler must not need the internet to clone it. The chart's
`devStack.gitDaemon` serves a bare copy over `git://` (which supports the
reconciler's `--depth 1`; RustFS's dumb HTTP does not), seeded from the
RustFS bucket `git-repos/campaigns-local.git` by an init container. The
update flow after a commit on the PoC branch of the campaigns repo:

```bash
cd ~/htr-test                                   # the PoC campaigns repo, branch local-k3s
git update-server-info                          # dumb-HTTP index for the seed clone
docker run --rm --network host \
  -e AWS_ACCESS_KEY_ID="$(kubectl -n htr-batch get secret htr-batch-s3 -o jsonpath='{.data.AWS_ACCESS_KEY_ID}' | base64 -d)" \
  -e AWS_SECRET_ACCESS_KEY="$(kubectl -n htr-batch get secret htr-batch-s3 -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' | base64 -d)" \
  -v "$PWD/.git:/repo:ro" amazon/aws-cli \
  --endpoint-url http://127.0.0.1:30900 s3 sync /repo s3://git-repos/campaigns-local.git
kubectl -n htr-batch rollout restart deploy/git-daemon      # re-seed
kubectl -n htr-batch create job --from=cronjob/htr-reconciler tick-now   # or wait ≤ 5 min
```

The `git-repos` bucket is credentials-only (the init hook removes any
anonymous policy on it). `reconciler.campaignsRepoUrl` is
`git://git-daemon.htr-batch.svc.cluster.local/campaigns-local`;
`reconciler.campaignsRepoWebUrl` can point at the branch on GitHub so the
browser's header link is useful. A hand-applied git daemon from before the
chart carried one must be deleted or adopted first (chart README,
"Adopting hand-applied resources"), and `network.reconciler.extraEgress`
for it dropped — the chart adds the reconciler → git-daemon rule itself.

## Two results bases

`publicResultsBase=http://localhost:30900/htr-results` is what a browser
on your laptop reaches through the SSH forward — but inside a pod
`localhost` is the pod. The reconciler therefore derives an
**`internal_results_base`** = `<S3_ENDPOINT>/<S3_BUCKET>` (the in-cluster
RustFS Service, from the S3 Secret) and hands Jobs the in-cluster URL of a
synthetic `images:` manifest, while `status.json` carries the public twin
of every bucket URL ([S3 Layout → URL rewriting](../reference/s3-layout.md#statusjson)).
On real AWS (`S3_ENDPOINT` empty) the two bases are the same URL. Anything
*you* put in a campaign file — a fixture manifest on the RustFS bucket, say
— must use the in-cluster form
(`http://rustfs.htr-batch.svc.cluster.local:9000/…`), because the Job, not
your browser, fetches it.

## Reaching it from a laptop

The node's address is not routable from a laptop, so forward both NodePorts
through a host that can reach it:

```bash
ssh -L 30800:<node-ip>:30800 -L 30900:<node-ip>:30900 <ssh-host>
```

Then `http://localhost:30800/` is the campaign browser and the viewer, and
`http://localhost:30900/htr-results/…` is what every link on it resolves to.
Both are required — the page comes from 30800, `status.json`, logs, images
and ALTO from 30900. This is exactly why `publicResultsBase` is a
`localhost` URL on the PoC ([Viewing Results](../getting-started/viewing.md#the-localhost-url-caveat)).
`kubectl port-forward` on the registry Service is the equivalent for
pushing images from a laptop.

## Hand-made resources the chart now renders

The first PoC applied the model-cache PVC, the `nvidia` RuntimeClass, the
kube-system device-plugin DaemonSet and the git daemon by hand. Helm refuses
to take over objects it did not create, so on this cluster each is either
**adopted** once (annotate `meta.helm.sh/release-name`/`release-namespace`,
label `app.kubernetes.io/managed-by=Helm` — commands in the chart README)
and then rendered (`modelCache.create=true`,
`devStack.nvidiaDevicePlugin.enabled=true`, `devStack.gitDaemon.enabled=true`),
or left outside (`modelCache.create=false` keeps using the existing
`htr-test-data`; the device plugin off). Adoption is the better end state:
the PVC gets `resource-policy: keep`, and the device-plugin image is
digest-pinned by the chart. The cache PVC was `chown -R 1000:1000`'d once
when the pods went non-root ([Security](security.md#cache-pvc-migration)).

## Upgrading the live release

```bash
helm upgrade htr charts/htrflow-batch -n htr-batch --reset-then-reuse-values \
  --set reconciler.image=127.0.0.1:30500/htrflow-reconciler@sha256:<digest> \
  --set viewer.image=127.0.0.1:30500/uv4@sha256:<digest>
make psa-labels
```

Always `--reset-then-reuse-values`: plain `--reuse-values` keeps the old
chart's defaults and once rendered every NetworkPolicy away. The 0.2.0
decisions for this cluster (PVC adoption, git daemon replacement, registry
uid, console, `allowTagImages`) are tabulated in the chart README.

## Gotchas collected on this node

- **`helm template` needs `network.apiServer.cidr`** (and `nodeCidrs`): the
  reconciler policy is built from a `lookup` that only works against a
  cluster.
- **Pipelines pinned to an image that predates `htrflow_batch.warmup`**
  cannot be submitted to any more — the warm-up gate is lazy for exactly
  that reason (only pipelines with volumes still to run are warmed), so old
  finished pipelines stay `done`; new work needs a new pipeline id on a
  current image.
- **Kueue quota is one GPU**: a second Job waits Suspended until the first
  finishes (a 480-spread volume runs ~12–13 s/page). `queued` with an idle
  GPU means Kueue is down, not busy.
- **Manual ticks** are safe: a second tick finds the Lease held and exits.
- **RustFS is single-disk**: the results bucket is one `local-path` PVC on
  this node (`rustfs-data`, 5 Gi by default, kept on uninstall). Fine for
  iteration; not an archive.
- Host prerequisites from the first PoC (inotify limits, `node-ip` pin,
  absolute eviction thresholds) are in [Prerequisites](../getting-started/index.md#bare-k3s-poc-path-host-gotchas).
