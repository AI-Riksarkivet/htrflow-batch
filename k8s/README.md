# htrflow-batch — PoC test manifests

Manifests used for the 2026-07-27 smoke test on bare k3s on dmlpai01
(see `../DESIGN.md` §13 for what was proven).

| File | What |
|---|---|
| `kueue-queues.yaml` | ResourceFlavor + ClusterQueue (cpu quota 2 = "2 GPUs") + LocalQueue `htr-batch` |
| `rustfs.yaml` | Namespace, RustFS S3 (Deployment/PVC/Service), `htr-batch-s3` Secret. Service is NodePort: S3 `:30900`, console `:30901` (`/rustfs/console/`, rustfsadmin/rustfsadmin) |
| `mini-wrapper.yaml` | ConfigMap with the miniature wrapper (`batch_run.py`): real page downloads → simulated HTR → streaming per-page ALTO upload → resume check → D8 verify gate → `manifest.json` last |
| `job-example.yaml` | One 4-page "volume" Job (suspend: true + queue label). Pages = Riksarkivet htr_demo images from HF |
| `../scripts/make_mock_manifest.py` | Generates a minimal IIIF P3 manifest (placeholder canvas dims) over 4 htr_demo fixture images uploaded to the `htr-fixtures` bucket, so the real wrapper can be smoke-tested with no live lbiiif dependency |
| `pipeline-demo-v1.yaml` | Immutable ConfigMap (D17) holding the `demo-v1` htrflow pipeline (yolo regions → yolo lines → TrOCR), no `Export` steps — the wrapper appends those |
| `job-real-wrapper.yaml` | Task 10 smoke Job for the real `htrflow-batch:v3` image against the mocked IIIF manifest above — PASSED (see DESIGN.md §13, "D16 wrapper smoke"; took 3 image rounds to fix two `driver.py` pipeline-construction bugs) |

## Bucket setup for the viewer (D19) — anonymous read + CORS

Applied 2026-07-27, persisted in RustFS's PVC; replay if the bucket is recreated.
Run inside an `amazon/aws-cli` pod (creds from `htr-batch-s3`, endpoint
`http://rustfs.htr-batch.svc.cluster.local:9000`):

```bash
# anonymous read on results (browser fetches manifest + ALTO directly)
aws s3api put-bucket-policy --bucket htr-results --policy '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"AWS":["*"]},"Action":["s3:GetObject"],"Resource":["arn:aws:s3:::htr-results/*"]}]}'
# CORS for the UV viewer origin (tighten * to the viewer origin beyond PoC)
aws s3api put-bucket-cors --bucket htr-results --cors-configuration '{"CORSRules":[{"AllowedOrigins":["*"],"AllowedMethods":["GET","HEAD"],"AllowedHeaders":["*"],"MaxAgeSeconds":3600}]}'
```

Verified: anonymous `GET http://10.16.51.53:30900/htr-results/...` → 200 with
`access-control-allow-origin: *` (GET and OPTIONS preflight). RustFS supports
the standard S3 CORS API (unlike MinIO). Note: writes still require credentials
— only `s3:GetObject` is anonymous.

## Replay

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl apply -f kueue-queues.yaml -f rustfs.yaml -f mini-wrapper.yaml
# create bucket once:
kubectl -n htr-batch run mkbucket --rm -i --restart=Never --image=amazon/aws-cli \
  --env=AWS_ACCESS_KEY_ID=rustfsadmin --env=AWS_SECRET_ACCESS_KEY=rustfsadmin \
  --command -- aws --endpoint-url http://rustfs.htr-batch.svc.cluster.local:9000 s3 mb s3://htr-results
kubectl apply -f job-example.yaml
k9s -n htr-batch   # watch
```

Kill-and-resume test: wait until ~2 ALTOs exist under
`demo-v0/vol-007/alto/`, force-delete the pod, watch the retry pod log
`resume: 2 pages already done` and converge to Complete.

Host prerequisites (already persisted on dmlpai01, see memory/design doc):
`fs.inotify.max_user_instances=1024` + `max_user_watches=1048576`
(`/etc/sysctl.d/99-k3s-inotify.conf`), k3s `node-ip` pinned in
`/etc/rancher/k3s/config.yaml` (hostname resolves IPv6-only).
