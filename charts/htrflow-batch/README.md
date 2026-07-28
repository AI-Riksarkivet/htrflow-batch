# htrflow-batch (Helm chart)

Kueue-gated batch HTR platform around the htrflow image: queues, pipeline
ConfigMaps, and a results viewer. Per-volume Jobs are submitted at runtime by
the orchestrator, not by this chart — `templates/job-example.yaml` is a smoke
artifact for PoC replay only (`exampleJob.enabled`, off by default).

## Prerequisites

- **Kueue CRDs must already be installed on the cluster.** This chart renders
  `ResourceFlavor` / `ClusterQueue` / `LocalQueue` objects
  (`templates/kueue.yaml`) but does not install the Kueue controller or its
  CRDs itself.
- Namespace creation is left to Helm (`--create-namespace`); the chart does
  not render a `Namespace` object for its own release namespace.

## Immutability warning

Pipeline ConfigMaps (`templates/pipelines.yaml`, one per key under
`.Values.pipelines`) are rendered with `immutable: true`. **Never reuse a
pipeline id with different content** — bump the id instead (D17). Changing
the content under an existing id will fail to apply against a live cluster
once the ConfigMap exists, and would silently desync already-submitted Jobs
that reference the old content if it somehow did apply.

## PoC replay (bare k3s, in-cluster devStack)

This reproduces the 2026-07-27 smoke test (see the
[development test log](../../docs/development/test-log.md)) using
the chart's optional `devStack.*` components (RustFS S3, an in-cluster
registry, the NVIDIA device plugin) instead of the standalone raw manifests
they replace.

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

Swap `helm template` for `helm install`/`helm upgrade --install` (same flags)
to actually apply against a cluster. `exampleJob.enabled=true` renders the
`htr-vol-301` Job with `suspend: true`; unsuspend it (or `kubectl create job
--from`) to run the smoke test, then watch with `k9s -n htr-batch`.

## Bucket setup for the viewer (D19) — anonymous read + CORS

Applied 2026-07-27, persisted in RustFS's PVC; replay if the bucket is
recreated. Run inside an `amazon/aws-cli` pod (creds from the
`s3.existingSecret` Secret, endpoint
`http://rustfs.<namespace>.svc.cluster.local:9000`):

```bash
# anonymous read on results (browser fetches manifest + ALTO directly)
aws s3api put-bucket-policy --bucket htr-results --policy '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"AWS":["*"]},"Action":["s3:GetObject"],"Resource":["arn:aws:s3:::htr-results/*"]}]}'
# CORS for the UV viewer origin (tighten * to the viewer origin beyond PoC)
aws s3api put-bucket-cors --bucket htr-results --cors-configuration '{"CORSRules":[{"AllowedOrigins":["*"],"AllowedMethods":["GET","HEAD"],"AllowedHeaders":["*"],"MaxAgeSeconds":3600}]}'
```

Verified: anonymous `GET http://10.16.51.53:30900/htr-results/...` → 200 with
`access-control-allow-origin: *` (GET and OPTIONS preflight). RustFS supports
the standard S3 CORS API (unlike MinIO). Note: writes still require
credentials — only `s3:GetObject` is anonymous.

## Replay

**Prerequisite for `exampleJob.enabled=true`:** a PVC named `htr-test-data` must exist in the release namespace (holds the HF model cache; 20Gi is plenty). On the dmlpai01 PoC it already exists.

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
helm upgrade --install htr charts/htrflow-batch -n htr-batch --create-namespace \
  --set devStack.rustfs.enabled=true --set devStack.registry.enabled=true \
  --set devStack.nvidiaDevicePlugin.enabled=true --set exampleJob.enabled=true \
  --set exampleJob.image=127.0.0.1:30500/htrflow-batch:v3 \
  --set exampleJob.manifestUrl=http://10.16.51.53:30900/htr-fixtures/mock-vol/manifest.json \
  --set publicResultsBase=http://localhost:30900/htr-results \
  --set viewer.image=127.0.0.1:30500/uv4:v3 \
  --set viewer.defaultManifest=http://localhost:30900/htr-results/demo-v1/mock-vol/iiif.json \
  --set-file pipelines.demo-v1=.docker/pipeline-demo-v1.yaml
# create bucket once:
kubectl -n htr-batch run mkbucket --rm -i --restart=Never --image=amazon/aws-cli \
  --env=AWS_ACCESS_KEY_ID=rustfsadmin --env=AWS_SECRET_ACCESS_KEY=rustfsadmin \
  --command -- aws --endpoint-url http://rustfs.htr-batch.svc.cluster.local:9000 s3 mb s3://htr-results
kubectl -n htr-batch patch job htr-vol-301 --type=json -p '[{"op":"replace","path":"/spec/suspend","value":false}]'
k9s -n htr-batch   # watch
```

Kill-and-resume test: wait until ~2 ALTOs exist under
`demo-v1/mock-vol/alto/`, force-delete the pod, watch the retry pod log
`resume: N pages already done` and converge to Complete.

Host prerequisites (already persisted on dmlpai01, see memory/design doc):
`fs.inotify.max_user_instances=1024` + `max_user_watches=1048576`
(`/etc/sysctl.d/99-k3s-inotify.conf`), k3s `node-ip` pinned in
`/etc/rancher/k3s/config.yaml` (hostname resolves IPv6-only).
