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
| `HTR_BUCKET` | the results bucket the compose stack writes to (on a cluster, the S3 Secret's `S3_BUCKET`) | the compose stack |
| `HTR_WEB_NODEPORT` | the web front's NodePort (chart `web.nodePort`) | `e2e`'s final `/api/v1/jobs` request |
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

The wrapper dockerfile builds its htrflow base itself, from the htrflow
commit it pins, so one target is enough on any architecture:

```bash
make poc-push          # build-wrapper + build-web for the host's architecture, push both, print digests
```

To try an htrflow change before it is pinned, build from a local checkout
instead; it replaces the pinned source and nothing is written into it:

```bash
make poc-push HTRFLOW_SRC=<htrflow-checkout>
```

The image then stamps the checkout's `git describe --tags --always --dirty`
as `HTRFLOW_BASE_REVISION`, because `manifest.json` only knows htrflow's
package version. The checkout's `pyproject.toml` must still be the one the
committed lock in `.docker/htrflow-base/` was made for; the build refuses
it otherwise rather than resolve afresh. After moving the pinned commit
(`HTRFLOW_REF` in the wrapper dockerfile), refresh the lock and review the
diff:

```bash
make lock-htrflow-base          # UV_LOCK_ARGS=--upgrade moves every pin
```

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
<campaigns-repo>/rendered` ([CLI](../reference/cli.md)); it is safe to
re-run. Repeat after every commit to that branch — nothing watches it for
you. `PRUNE=1` cancels everything the checkout does not contain, so only run
it against the whole repo. `make e2e` waits for the warm-up Jobs, then polls every campaign Job
until it is Complete or Failed (`CAMPAIGN_TIMEOUT` seconds, default 3600).
It fails if any Job ends Failed, if no campaign Job exists, or if `kubectl`
cannot read one. Otherwise it finally requests `/api/v1/jobs` from the web front's NodePort on the
machine running `make`. Editing an already-rendered campaign's volume list in
place hits the append-only rule
([Campaign & Pipeline YAML](../reference/campaign-yaml.md)).

## Two S3 endpoints, two results bases

On a dev cluster the browser and the pods reach the same RustFS at different
addresses. `publicResultsBase` is the forwarded address a browser on your
workstation uses ([View results](../getting-started/viewing.md#exposing-the-web-front));
the pods cannot resolve it. The wrapper writes through the S3 Secret's
in-cluster endpoint anyway, but the read API needs the in-cluster address
for `progress.json`, or the campaign page silently never shows a running
volume's progress:

```yaml
web:
  internalResultsBase: http://rustfs.<namespace>.svc.cluster.local:9000/<bucket>
```

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

A model-cache PVC, `nvidia` RuntimeClass or device-plugin DaemonSet applied
by hand must be adopted into the release or left outside it
(`modelCache.create`, and `nvidiaDevicePlugin.enabled` in the devstack chart;
[Deploy → Model cache](../getting-started/deploy.md#model-cache); the
commands are in each chart's README). A cache PVC root-running pods wrote to
needs a one-time `chown -R 1000:1000` before the first warm-up.

## Upgrading the live release

```bash
helm upgrade <release> charts/htrflow-batch -n <namespace> --reset-then-reuse-values \
  --set web.image=<registry>/htrflow-web@sha256:<digest>
make psa-labels
```

The same `--reset-then-reuse-values` rule holds for `charts/htrflow-devstack`
([Deploy → Upgrading](../getting-started/deploy.md#upgrading)).

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
- **`make install-devstack NVIDIA_DEVICE_PLUGIN=false` refuses while GPU
  pods run.** Disabling the plugin deletes the chart-managed `nvidia`
  RuntimeClass and device-plugin DaemonSet, which takes down any GPU pod
  running on them. The target looks for a pod outside
  `kube-system` that is Running or Pending and uses `runtimeClassName:
  nvidia` or requests `nvidia.com/gpu`, and exits non-zero if it finds one,
  or if it cannot list the pods to find out; `FORCE=1` skips the check.
- **RustFS is single-disk.** The results bucket is one PVC on the node
  (`rustfs-data`, sized by `rustfs.storage.size`, kept on uninstall). Fine
  for iteration; not an archive.
