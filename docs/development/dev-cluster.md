# Dev cluster

The contributor loop on a single-node GPU dev cluster: the platform installed
as one release of `charts/htrflow-batch`, and every dependency it needs from
outside provided by `charts/htrflow-devstack` — RustFS as the S3 store, an
image registry in its own `registry` namespace, and the NVIDIA RuntimeClass
and device plugin. Kueue is installed separately (`make install-kueue`), and
so is Kyverno (`make install-kyverno`, which `make install-devstack` runs
first); neither chart renders their controllers. Installing the stack is
[Try it → on a dev cluster](../getting-started/try-it.md#on-a-dev-cluster-the-devstack-chart);
this page is everything around it.
The devstack is not production-shaped — its caveats are in
[Security](../how-it-works/security.md).

## `.env`

Cluster constants for the Makefile and the compose stack live in a
git-ignored `.env` at the repository root. `.env.example` holds the defaults
and is loaded first, so a key missing from `.env` keeps its default. Copy it
and adjust:

| Key | Meaning | Used by |
|---|---|---|
| `HTR_RELEASE`, `HTR_NAMESPACE` | Helm release name and namespace of the platform (the devstack release is `<release>-devstack` in the same namespace) | `helm-template`, `install-devstack`, `psa-labels`, `e2e` |
| `HTR_REGISTRY` | the registry address images are pushed to and pulled from, as the node sees it — `<registry>` on this page | `build-wrapper`, `build-web`, `poc-push` (image names) |
| `HTR_REGISTRY_NODEPORT` | the registry Service's NodePort (devstack `registry.nodePort`) | reference for your own forwards |
| `HTR_S3_ENDPOINT` | the devstack S3 endpoint as the machine running `make` reaches it | `scripts/make_mock_manifest.py`; your own `aws` calls |
| `HTR_S3_NODEPORT` | RustFS's S3 NodePort (devstack `rustfs.nodePortS3`) | reference for your own forwards |
| `HTR_BUCKET` | the results bucket (chart `s3.bucket`) | the compose stack |
| `HTR_WEB_NODEPORT` | the web front's NodePort (chart `web.nodePort`) | `e2e`'s final `/api/v1/jobs` request |
| `HTR_DATA_PVC` | the model-cache PVC name (chart `modelCache.name`) | not read by any target |
| `HTRFLOW_DIR` | a local htrflow checkout, for a base built from source | the base target below; the base's `git describe` |
| `HTR_DEV_S3_ACCESS_KEY`, `HTR_DEV_S3_SECRET_KEY` | throwaway RustFS root credentials | the compose stack only — never a cluster |

The chart takes the same values through `--set` or a values file; the Python
packages take theirs from their own environment
([Configuration](../reference/configuration.md)).

## The GPU wrapper image

The wrapper must be built for the node's own architecture. An image built
for another architecture runs on the node only under CPU emulation, where
the GPU is unreachable and `uv` crashes, so the build always runs natively
on a machine of the node's architecture
([Releasing](releasing.md#one-dockerfile-every-architecture)).

**When upstream htrflow publishes a base for the node's architecture**, the
wrapper dockerfile uses it and one target is enough:

```bash
make poc-push          # build-wrapper + build-web for the host's architecture, push both, print digests
```

**When it does not**, build the base from an htrflow checkout first:

```bash
# htrflow's lockfile is not committed: create it in that checkout yourself.
# The target refuses to run without one rather than write into a working tree it does not own.
(cd <htrflow-checkout> && uv lock)
make build-htrflow-base-arm64 HTRFLOW_DIR=<htrflow-checkout>   # docker/htrflow.dockerfile from the checkout
make poc-push
```

On such a node `make build-wrapper` passes the locally built base to the
dockerfile and stamps `HTRFLOW_BASE_REVISION` (`git -C $HTRFLOW_DIR describe
--tags --always --dirty`) into the image, because `manifest.json` only knows
htrflow's package version while a source-built base usually runs a later
commit. CI builds the same recipe in a throwaway clone at a pinned htrflow
commit, so the recipe here and the one that publishes images cannot drift.
The resolved dependency set can: the pin fixes htrflow's source, not what
`uv lock` resolves on the day. The explicit torch and torchvision pins in the
wrapper dockerfile turn such a drift into a build failure instead of a silent
change.

`poc-push` prints the wrapper and web digests. The wrapper's goes into
`pipelines/<id>.yaml` in the campaigns repo, the web front's into
`web.image`. While iterating, `security.allowTagImages=true` accepts a tag
(`IMAGE_TAG`, default `dev`) instead of a digest; a tag is then pulled on
every rollout.

### Rebuilding the web image

The campaign browser and the Universal Viewer are baked into the web image
next to the read API, so any UI change needs a rebuild and a new
`web.image`:

```bash
make build-web                              # bun-builds the SPA, clones + patches the viewer, tags <registry>/htrflow-web:<tag>
docker push <registry>/htrflow-web:<tag>    # prints the digest to pin as web.image
dagger call build-web                       # the same dockerfile, through the dagger engine
```

The image runs unprivileged and listens on port 8081; the chart's
`containerPort` and Service `targetPort` follow it, and `web.nodePort` is the
outside port. Then [upgrade the release](#upgrading-the-live-release) with
the new digest.

## The in-cluster registry

The devstack registry is a NodePort Service `registry` (port 5000) in the
`registry` namespace. It is unauthenticated and serves plain HTTP, so:

- the node's container runtime must be allowed to pull from `<registry>`
  without TLS — runtimes typically allow that for a loopback address and
  need explicit registry configuration for any other;
- `docker push` from the node to `<registry>` needs the same allowance in
  the Docker daemon, or a loopback address;
- from a workstation, `kubectl -n registry port-forward svc/registry
  <local-port>:5000` gives you a push address.

The registry does no garbage collection of its own. To reclaim space,
delete tags over the registry API, then run `registry garbage-collect
/etc/distribution/config.yml` in the registry pod. Pipeline files and
`web.image` pin `<registry>/…@sha256:<digest>` references, which resolve
only on this cluster.

## Applying a campaigns repo

Because its pipeline files pin digests in `<registry>`, a dev cluster's
campaigns repo belongs on its own branch or in its own repository, never on
a shared `main`. Nothing in the cluster clones it; render and apply it from a
checkout with a kubeconfig for the cluster:

```bash
export KUBECONFIG=<kubeconfig>
make campaigns-apply DIR=<campaigns-repo>           # render + apply, pipelines first
make campaigns-apply DIR=<campaigns-repo> PRUNE=1   # also cancel what this render no longer contains
make e2e DIR=<campaigns-repo>                       # validate, apply, wait for every campaign Job to finish
```

`campaigns-apply` runs `htrflow-campaigns apply <campaigns-repo> --out
<campaigns-repo>/rendered`, which renders into `rendered/pipelines` and
`rendered/campaigns`, applies them server-side, and syncs each campaign's
pause. Repeat after every commit to that branch — nothing watches it for
you. `make e2e` waits for the warm-up Jobs, then polls every campaign Job
until it is Complete or Failed (`CAMPAIGN_TIMEOUT` seconds, default 3600).
It fails if any Job ends Failed, if no campaign Job exists, or if `kubectl`
cannot read one. Otherwise it finally requests `/api/v1/jobs` from the web front's NodePort on the
machine running `make`. Editing an already-rendered campaign's volume list in
place hits the append-only rule
([Campaign & Pipeline YAML](../reference/campaign-yaml.md)).

## Two S3 endpoints, two results bases

On a dev cluster the browser and the pods reach the same RustFS at different
addresses. `publicResultsBase` is what a browser on your workstation reaches
through its forward; neither pod that touches S3 can use that address:

- The **wrapper** writes through `S3_ENDPOINT`, the in-cluster RustFS Service
  address from the S3 Secret. `PUBLIC_RESULTS_BASE` is only embedded as
  browser-facing text (`viewer_url` in `manifest.json`, the `id` inside a
  published `iiif.json` or synthetic manifest); nothing rewrites URLs later,
  because nothing stores a derived copy of them.
- The **web front**'s read API reads `progress.json` from S3 for a running
  volume's page counts ([Signals](../how-it-works/signals.md)).
  `HTRFLOW_PUBLIC_RESULTS_BASE` is what every browser link is built from, so
  it has to stay the forwarded address — and the pod cannot resolve that.
  `web.internalResultsBase` sets `HTRFLOW_INTERNAL_RESULTS_BASE` to the
  in-cluster address instead:

  ```yaml
  web:
    internalResultsBase: http://rustfs.<namespace>.svc.cluster.local:9000/<bucket>
  ```

  `rustfs` on port 9000 is the Service `charts/htrflow-devstack` renders.
  Left unset, it defaults to `publicResultsBase`, which is right on a
  cloud object store (empty `S3_ENDPOINT`, one address for everyone) and
  wrong here. There is no error — only a campaign page that never shows a
  running volume's progress.

Anything you put in a campaign file — a fixture manifest on the RustFS
bucket, say — must use the in-cluster form
(`http://rustfs.<namespace>.svc.cluster.local:9000/…`), because the pod, not
your browser, fetches it.

## Reaching it from a workstation

The campaign browser needs two addresses in the browser: the web front
(page, viewer, `/api/v1/…`) and the results bucket (manifests, images,
ALTO, logs — every link on the page). When the node is not routable from
your workstation, forward both, either through a host that reaches the node:

```bash
ssh -L <web-port>:<node-address>:<web-nodeport> \
    -L <s3-port>:<node-address>:<s3-nodeport> <ssh-host>
```

or with `kubectl`:

```bash
kubectl -n <namespace> port-forward svc/htrflow-web <web-port>:8081
kubectl -n <namespace> port-forward svc/rustfs <s3-port>:9000
```

Then `http://<workstation>:<web-port>/` is the campaign browser, and
`publicResultsBase` must be `http://<workstation>:<s3-port>/<bucket>` — the
address the browser uses for the S3 forward, which is why it differs from
the in-cluster one above. `<ssh-host>` is any machine you can reach that
reaches the node; the `-L` targets resolve on its side. Exposing the web
front without a tunnel is covered in
[View results](../getting-started/viewing.md#exposing-the-web-front).

## Resources that already exist on the cluster

Helm refuses to take over objects it did not create. On a cluster where the
model-cache PVC, the `nvidia` RuntimeClass or the device-plugin DaemonSet
were applied by hand, either **adopt** each once (annotate
`meta.helm.sh/release-name` and `meta.helm.sh/release-namespace`, label
`app.kubernetes.io/managed-by=Helm` — the commands are under "Adopting
hand-applied resources" in each chart's README) and let the chart render it
(`modelCache.create=true` in `charts/htrflow-batch`,
`nvidiaDevicePlugin.enabled=true` in `charts/htrflow-devstack`), or leave it
outside (`modelCache.create=false` with `modelCache.name` set to the existing
PVC; the device plugin off). Adoption is the better end state: the PVC gets
`helm.sh/resource-policy: keep`, and the chart pins the device-plugin image
by digest. The pods run as uid 1000, and not every volume provisioner
honours `fsGroup`: a cache PVC (or registry data PVC) that root-running pods
wrote to, or one on such a provisioner, needs `chown -R 1000:1000` once from
a throwaway pod before the first warm-up
([Security](../how-it-works/security.md)).

## Upgrading the live release

```bash
helm upgrade <release> charts/htrflow-batch -n <namespace> --reset-then-reuse-values \
  --set web.image=<registry>/htrflow-web@sha256:<digest>
make psa-labels
```

Always `--reset-then-reuse-values` (or a full values file), for
`charts/htrflow-devstack` too: plain `--reuse-values` keeps the old chart's
defaults, so a new default never reaches an existing release, and a missing
new value can render whole features away — the chart fails loudly when
`network` is absent for that reason. A release that runs with the
Kyverno policies off needs `--set security.policies.allowDisabled=true` once:
the chart refuses to render policies that are off without it. Upgrade notes per chart version are in
the chart READMEs ([Releasing](releasing.md#chart-releases)).

## Gotchas

- **A node shared with other services may need host-level settings.** Raise
  the inotify instance limit — when other services exhaust it, the kubelet
  fails to register the node without a clear error — and prefer absolute
  over percentage-based kubelet eviction thresholds on a large shared disk.
  The values belong to the host's configuration, not this repository.
- **`helm template` needs `network.apiServer.cidr`**, and wants
  `network.nodeCidrs`: the NetworkPolicies take both from `lookup` calls
  that return nothing without a cluster. The render fails without the API
  server CIDR, and without node CIDRs its public-egress rule excludes no
  node addresses.
- **A pipeline's image must contain `htrflow_batch.warmup`.** Every campaign
  pod's init container waits for the marker file the warm-up writes, so a
  pipeline pinned to an image without that module never starts. Pipelines are
  immutable: new work needs a new pipeline id on a current image.
- **`queued` with an idle GPU means Kueue is down, not busy.** With a GPU
  quota of one (sized in `queue.*`), a second index waits suspended until the
  first finishes; a dead Kueue controller looks the same, so check it first.
- **`make campaigns-apply` is safe to re-run.** `render` is a pure function
  and a server-side apply is idempotent, against running, completed and
  failed Jobs alike. That holds because the converter caps `parallelism` at
  the configured window itself instead of relying on Kueue partial admission,
  which would shrink `spec.parallelism` on the live Job and make every later
  apply of the unchanged rendered file fail.
- **Cancelling needs `PRUNE=1`.** An apply on its own never deletes.
  `--prune` lists every Job and ConfigMap in the namespace labelled as
  managed by the converter and deletes the ones this render did not
  produce. Only run it against the *whole* campaigns repo: against a partial
  checkout it cancels everything the checkout does not contain.
- **`make install-devstack NVIDIA_DEVICE_PLUGIN=false` refuses while GPU
  pods run.** Disabling the plugin deletes the chart-managed `nvidia`
  RuntimeClass and device-plugin DaemonSet, which takes down any GPU pod
  running on them. The target looks for a pod outside
  `kube-system` that is Running or Pending and uses `runtimeClassName:
  nvidia` or requests `nvidia.com/gpu`, and exits non-zero if it finds one,
  or if it cannot list the pods to find out; `FORCE=1` skips the check.
- **Pausing is `suspend: true` in the campaign file plus the apply.** The
  rendered `spec.suspend` alone does not hold — Kueue owns that field for an
  admitted Workload and undoes a change within seconds — so the pause sync in
  `htrflow-campaigns apply` patches the Workload's `spec.active`. Never
  `kubectl edit` the Job
  ([Campaign & Pipeline YAML](../reference/campaign-yaml.md)).
- **RustFS is single-disk.** The results bucket is one PVC on the node
  (`rustfs-data`, sized by `rustfs.storage.size`, kept on uninstall). Fine
  for iteration; not an archive.
