# Try it

Two ways to see htrflow-batch work before a production-shaped
[Deploy](deploy.md). The first needs only Docker. The second needs a cluster
with one NVIDIA GPU node.

## Without a cluster: Docker Compose

The compose stack runs the wrapper once, end to end, on your own machine: an
S3 server, a fixture volume, one wrapper run on the CPU, and the web front.

```bash
make compose-up      # background: S3 (RustFS) + fixtures + one wrapper run + web front
make compose-down    # stop it and drop its volumes
```

```bash
make compose-smoke   # foreground: build the wrapper, run it to completion, check the web front, tear down
```

`make compose-smoke` is the local end-to-end check. It builds the wrapper
image from your checkout and runs it until it exits, failing if the wrapper
fails. Then it starts the web front, fetches `http://localhost:8080/uv.html`
and removes the stack. `make compose-test` runs the same web-front health
check through Dagger, using registry-pullable images only.

What `.docker/docker-compose.yml` starts:

| Service | What it does |
|---|---|
| `rustfs` | An S3 server. Its API is on host port **19000**, and 19001 is mapped for its console. The ports are not RustFS's usual 9000 and 9001, so the stack does not collide with another S3 server on the same machine. |
| `fixtures-init` | Creates the buckets, uploads a few sample pages, publishes a mock IIIF manifest for volume `mock-vol`, and applies the anonymous-read policy and CORS (`scripts/compose_init.py`). |
| `wrapper` | Runs volume `mock-vol` through pipeline `demo-v1` (`.docker/pipeline-demo-v1.yaml`) with `MAX_PAGES: "1"`. With no GPU, htrflow falls back to the CPU, which is far slower, so the stack does one page. |
| `web` | The web front on host port **8080**, in site-only mode (`HTRFLOW_WEB_SITE_ONLY`). With no API server to read, `/`, `/log` and `/uv.html` are served and `/api/v1/…` answers 503. |

With `make compose-up` running, open the result in the viewer:

```
http://localhost:8080/uv.html#?manifest=http://localhost:19000/htr-results/demo-v1/mock-vol/iiif.json
```

The transcription and its ALTO overlay load from the bucket on
`localhost:19000`. The page image itself does not show. The mock manifest
names it at `rustfs:9000`, the address the wrapper container fetches from,
which a browser on the host cannot resolve.

The stack's RustFS credentials are throwaway values from `.env.example`
(`HTR_DEV_S3_ACCESS_KEY`, `HTR_DEV_S3_SECRET_KEY`). Never reuse them for a
cluster. The compose project is named `htrflow-batch-smoke`, so
`docker compose down` cannot touch another project that also runs from a
directory called `.docker`.

## On a dev cluster: the devstack chart

`charts/htrflow-devstack` gives a disposable cluster the infrastructure a
real deployment gets from elsewhere, so the whole path runs on one node:

- an S3 server (RustFS) on NodePort 30900. A hook creates the bucket and
  applies the bucket policy and CORS on every install and upgrade. The S3
  Secret, with generated credentials, is written for you.
- an unauthenticated image registry in the `registry` namespace, on NodePort
  30500, for iterating on your own images.
- the NVIDIA device plugin and the `nvidia` RuntimeClass.

None of it is production-shaped. Never install it next to real data or real
credentials. The [Security](../how-it-works/security.md) page lists its
caveats.

You need a Kubernetes cluster with one NVIDIA GPU node that the htrflow
image's CUDA build supports, a kubeconfig for it, and `kubectl`, `helm`,
`make` and `uv`. The published, signed images are used, so nothing has to be
built.

### 1. Install the prerequisites and the devstack

```bash
git clone https://github.com/AI-Riksarkivet/htrflow-batch && cd htrflow-batch
make install            # uv workspace: the converter CLI (htrflow-campaigns)
make install-kueue      # Kueue: the queue and GPU quota
make install-devstack   # Kyverno, then the devstack chart: S3, registry, device plugin
```

The cluster targets take the release name and namespace from `HTR_RELEASE`
and `HTR_NAMESPACE` in the repo-root `.env`. The defaults are `htr` and
`htr-batch`, which a new campaigns repo also uses. `<namespace>` below is
that namespace.

`make install-devstack` options:

- `KYVERNO=false` skips Kyverno.
- `NVIDIA_DEVICE_PLUGIN=false` leaves the device plugin and RuntimeClass out,
  for a cluster that already has them. The target refuses this while GPU
  pods are running, because deleting the RuntimeClass and DaemonSet takes
  those pods down. `FORCE=1` overrides the check.
- A device plugin or RuntimeClass that was applied by hand must first be
  adopted into the release, or kept outside it. The commands are under
  "Adopting hand-applied resources" in `charts/htrflow-devstack/README.md`.

### 2. Install htrflow-batch

```bash
helm upgrade --install htr charts/htrflow-batch -n <namespace> \
  --set publicResultsBase=http://<node-address>:30900/htr-results \
  --set web.internalResultsBase=http://rustfs.<namespace>.svc.cluster.local:9000/htr-results \
  --set network.apiServer.cidr=<apiserver-address>/32 \
  --set network.iiifCidrs='{<iiif-source-cidr>}' \
  --set network.clusterCidrs='{<pod-cidr>,<service-cidr>}'
make psa-labels
```

- `<node-address>` is the address your browser reaches the node at. The
  browser loads the web front from port 30800 and every result from port
  30900.
- `web.internalResultsBase` points the read API at RustFS's in-cluster
  Service. Without it, the read API resolves `publicResultsBase` from inside
  its pod and shows no progress for any campaign.
- `<apiserver-address>` is the API server as pods reach it. On a single-node
  cluster this is usually the node's address.
- `network.iiifCidrs` must cover the host you transcribe from. Campaign pods
  can reach nothing else.

The security policies stay off here. Kyverno is installed, but no policy is
rendered. To turn them on, allow the images this namespace runs:

```bash
  --set security.policies.enabled=true \
  --set security.allowedImageRepos='{docker.io/riksarkivet/,rustfs/,docker.io/amazon/aws-cli}'
```

Add `<registry>/` when you run images from the devstack registry. `make
poc-push` builds the wrapper and web images for the architecture of the
machine it runs on, pushes them to the registry named by `HTR_REGISTRY` in
`.env`, and prints the digests to pin.
While iterating on the web front, `--set security.allowTagImages=true` lets
`web.image` be a tag, which is then pulled on every rollout.
[Dev cluster](../development/dev-cluster.md) covers that loop.

### 3. Describe what to transcribe

```bash
uv run htrflow-campaigns init my-campaigns
```

Edit two files in `my-campaigns/`:

- In `converter.yaml`, set `public_results_base` to the same URL as
  `publicResultsBase`.
- In `campaigns/demo.yaml`, list your volumes. `pipelines/demo-v1.yaml`
  already pins the published wrapper image by digest.

```yaml title="campaigns/demo.yaml"
pipeline: demo-v1
volumes:
  - id: <volume-id>
    manifest: <iiif-manifest-url>
```

A bare string instead of `id:` plus `manifest:` is a reference code, expanded
through `source_template` in `converter.yaml`. Set that template for your
source before you use bare references.
[Campaign & Pipeline YAML](../reference/campaign-yaml.md) has every format.

```bash
make campaigns-apply DIR=my-campaigns
```

This renders the repo into `my-campaigns/rendered/` and applies it. The
warm-up Job downloads the pipeline's models into the model cache. Kueue
admits the campaign, and each campaign pod's `warmup-wait` init container
holds until that warm-up has finished.

### 4. Watch it and open the result

- `http://<node-address>:30800/` is the campaign browser: every campaign,
  volume and page. A done volume's **open** link goes to the viewer, with the
  transcription next to the page image.
- `kubectl -n <namespace> get workloads,jobs,pods` shows the same from the
  cluster.
- The RustFS credentials are in the S3 Secret:
  `kubectl -n <namespace> get secret htr-batch-s3 -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' | base64 -d`.

If the node is not directly reachable from your browser, see
[Exposing the web front](viewing.md#exposing-the-web-front).

### Check resume

Once a few ALTO files exist under `<namespace>/<pipeline>/<volume>/alto/` in
the bucket, force-delete the running campaign pod:

```bash
kubectl -n <namespace> delete pod <pod> --force --grace-period=0
```

The retry pod's log says `[<volume>] resume: <n> done, <m> to process`. It
does not redo the published pages, and the index still reaches `Complete`.

## Next

- [Deploy](deploy.md): your own S3, the policies on, the hardening steps.
- [Run a campaign](campaigns.md): a campaigns repo of your own, applied
  through CI.
