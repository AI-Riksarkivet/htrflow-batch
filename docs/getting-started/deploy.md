# Deploy

Everything ships as a single helm chart, `charts/htrflow-batch`. Per-volume
Jobs are submitted at runtime (see [Run a Volume](run-a-volume.md)) — the
chart itself only deploys the queueing, viewer, and (optionally) PoC
support infrastructure.

## Production-shaped install

```bash
helm install htr charts/htrflow-batch -n htr-batch --create-namespace \
  --set s3.endpoint=<your-s3-endpoint> \
  --set publicResultsBase=<browser-reachable results base URL> \
  --set image.repository=<your-registry>/htrflow-batch --set image.tag=<pinned-digest-or-tag> \
  --set viewer.defaultManifest=<default iiif.json URL, optional>
```

Kueue CRDs must already be installed on the cluster — see
[Prerequisites](index.md). The chart does not install the Kueue controller.
`s3.existingSecret` (default `htr-batch-s3`) must already exist with
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` keys. Queue quota is a plain
list of covered resources under `queue.resources` (defaults to `cpu`/
`memory`; add `nvidia.com/gpu` for a real GPU-gated cluster):

```yaml
queue:
  resources:
    - name: cpu
      quota: 2
    - name: memory
      quota: 4Gi
```

Pipeline ConfigMaps are rendered immutable, one per id under
`.Values.pipelines` — never reuse a pipeline id with different content,
bump the id instead. See the
[chart README](https://github.com/carpelan/test/blob/main/charts/htrflow-batch/README.md)
for the full immutability rationale.

## PoC replay (bare k3s, in-cluster devStack)

This reproduces the 2026-07-27/28 smoke test using the chart's optional
`devStack.*` components (RustFS S3, an in-cluster registry, the NVIDIA
device plugin) instead of standalone raw manifests:

```bash
helm template htr charts/htrflow-batch -n htr-batch \
  --set devStack.rustfs.enabled=true --set devStack.registry.enabled=true \
  --set devStack.nvidiaDevicePlugin.enabled=true --set exampleJob.enabled=true \
  --set exampleJob.image=127.0.0.1:30500/htrflow-batch:v3 \
  --set exampleJob.manifestUrl=http://10.16.51.53:30900/htr-fixtures/mock-vol/manifest.json \
  --set publicResultsBase=http://localhost:30900/htr-results \
  --set viewer.image=127.0.0.1:30500/uv4:v3 \
  --set viewer.defaultManifest=http://localhost:30900/htr-results/demo-v1/mock-vol/iiif.json \
  --set-file pipelines.demo-v1=.docker/pipeline-demo-v1.yaml
```

Swap `helm template` for `helm install`/`helm upgrade --install` (same
flags) to actually apply against a cluster. `exampleJob.enabled=true`
renders the `htr-vol-301` Job with `suspend: true`; unsuspend it (or
`kubectl create job --from`) to run the smoke test.

Push the wrapper image into the in-cluster registry with:

```bash
make poc-push
```

(builds `.docker/htrflow-batch.dockerfile` and pushes to
`127.0.0.1:30500/htrflow-batch:dev` — adjust the tag to match
`exampleJob.image` above.)

### Bucket policy and CORS for the viewer

The results bucket needs anonymous read plus CORS so the browser can fetch
the manifest and ALTO directly, and the UV4 viewer's own image needs to
come from somewhere pullable. These replay commands (formerly in the
retired `k8s/README.md`) now live in the
[chart README](https://github.com/carpelan/test/blob/main/charts/htrflow-batch/README.md)
(see "Bucket setup for the viewer") — run them inside an `amazon/aws-cli`
pod against the RustFS endpoint before opening the viewer for the first
time.

## Local compose smoke stack

No Kubernetes cluster needed — see [Run a Volume](run-a-volume.md#local-compose-alternative)
for the `make compose-up` / `make compose-smoke` path.
