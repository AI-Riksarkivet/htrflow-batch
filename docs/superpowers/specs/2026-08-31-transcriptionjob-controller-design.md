# TranscriptionJob controller — design (B63, #2978)

Status: draft for review · 2026-08-31 · replaces the CronJob reconciler and its
S3 state files with a CRD + controller. Approved direction in chat; this is the
written form.

## 1. Goal and non-goals

**Goal.** A transcription job is a Kubernetes object. A controller turns it into
Kueue-queued wrapper Jobs, records outcomes in the object's status, and nothing
else keeps job state. The codebase shrinks; ATRaaS (T01–T18) builds on the
controller instead of on JSON files in a bucket.

**Keep unchanged:** the wrapper (`packages/wrapper`) and its env/exit contract,
S3 result layout (`<pipeline>/<volume>/…`, `manifest.json`), Kueue queues,
NetworkPolicies, supply chain, viewer image, GitOps of campaigns.

**Non-goals (later stories):** external API with auth (T01/T03), uploads (T04),
per-org quotas beyond one Kueue queue per namespace (T05), retention (T10).
The design leaves room for them; it does not build them.

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Go + controller-runtime**, kubebuilder layout, in `packages/controller/` (own Go module) | Copies `rask-operator` (proven here); generated CRDs/deepcopy, informer cache, envtest. Python/kopf would keep one language but reinvents watch/cache/leader election. |
| D2 | **Two CRDs**: cluster-scoped `Pipeline`, namespaced `TranscriptionJob` | Pipelines are Riksarkivet-curated and shared across tenants (home for the model registry, T12). Jobs belong to a namespace = organisation (T13). |
| D3 | **One CR per job, ≤ 10 000 volumes**; larger campaigns are split by the generator | Keeps status < ~250 KB, far under etcd's 1.5 MiB; matches the brief's job size. |
| D4 | **Per-volume state lives in `status.volumes`** (one short code each); per-page state stays in the wrapper's `manifest.json` in S3 | Bounded, queryable with `kubectl`, no S3 index in the controller. |
| D5 | **Only in-flight volumes are Jobs** (`spec.window`, default from controller config) | Same as today's window; keeps Kueue Workload count small. |
| D6 | **Accounting = Prometheus counters** set at completion; no database | T14 needs per-namespace/pipeline totals, not per-page rows. |
| D7 | **Cancel = `spec.paused: true`** (Jobs deleted, done pages kept) and **delete = GC** via ownerReferences + finalizer | T06 semantics without a new verb. |
| D8 | **Status page reads `/api/v1/jobs` served by the controller** (read-only JSON projection of CRs), proxied by the viewer nginx | `status.json` goes; this endpoint is the seed of T03. |
| D9 | **Campaign repo holds CR manifests**; Argo CD applies them in dev/prod, `make campaigns-apply` on the PoC | The controller never clones Git. Migration script converts today's YAML. |
| D10 | **devStack leaves the prod chart** into `charts/htrflow-devstack` | 470 template lines of PoC-only objects out of the install path. |

## 3. API

Group `htrflow.riksarkivet.se`, version `v1alpha1`.

### Pipeline (cluster-scoped)
```yaml
apiVersion: htrflow.riksarkivet.se/v1alpha1
kind: Pipeline
metadata: {name: demo-v1}
spec:
  image: docker.io/riksarkivet/htrflow-batch@sha256:…   # digest required (webhook-free: validated by controller, condition on failure)
  steps: [...]                                          # htrflow steps, verbatim (x-kubernetes-preserve-unknown-fields)
  modelRevision: ""                                     # optional pin, as today
status:
  conditions: [{type: Ready, status: "True"|"False", reason, message}]
  pipelineSha256: <sha of rendered steps yaml>          # the drift ground truth, as today
```
The controller renders `steps` to a ConfigMap `pipeline-<name>` in each namespace
that has a job using it (mounted at `/config/pipeline.yaml`, unchanged for the wrapper).

### TranscriptionJob (namespaced)
```yaml
apiVersion: htrflow.riksarkivet.se/v1alpha1
kind: TranscriptionJob
metadata: {name: kyrkbocker-2026-q3, namespace: riksarkivet}
spec:
  pipeline: demo-v1
  volumes:                                # ≤ 10000; ids: label-value alphabet, unique
    - id: R0001203                        # bare id → https://lbiiif.riksarkivet.se/arkis!<id>/manifest
    - {id: loc-mal2459400, manifest: https://…/manifest.json}
    - {id: htr-demo, images: [https://…/1.jpg, https://…/2.jpg]}   # synthetic manifest published to sources/, as today
  window: 20                              # max in-flight Jobs for this CR (controller caps globally)
  maxAttempts: 3
  priority: ""                            # Kueue WorkloadPriorityClass name (B18)
  paused: false                           # true = cancel: delete Jobs, keep results
status:
  phase: Pending|Validating|Running|Paused|Succeeded|Failed
  counts: {total, pending, running, done, failed, invalid}
  volumes: {R0001203: "D", loc-mal2459400: "R", htr-demo: "F:2"}   # P pending · V validating · R running · D done · F:<attempts> failed · I invalid
  failures:                               # last 50, newest first
    - {volume, attempt, reason, exitCode, log: <s3 key>, at}
  resultsBase: https://…/results/          # publicResultsBase + pipeline
  observedGeneration, conditions: [{type: Ready|Progressing|Stalled}]
```
Validation rules from `parse.py` (http(s) only, id alphabet, uniqueness,
pipeline exists) move into the CRD schema where OpenAPI can express them
(pattern, maxItems, enum) and into the controller's admission pass for the rest
(condition `Ready=False`, reason `InvalidSpec`, nothing submits).

## 4. Controller behaviour

One reconciler per CRD, leader-elected, single replica.

**TranscriptionJob reconcile** (event-driven; requeue only on transient errors):
1. Finalizer present? else add. `deletionTimestamp` set → delete owned Jobs, remove finalizer.
2. `spec.paused` → delete running Jobs (SIGTERM → wrapper exit 143 keeps done pages), phase `Paused`, stop.
3. Pipeline `Ready`? else condition `Stalled/PipelineNotReady`, stop (re-run when Pipeline changes — watch with a mapping).
4. **Validate** volumes still `P`: HEAD/GET the manifest with the same caps as today (`MANIFEST_MAX_BYTES`), permanent failures → `I`, transient → stay `P` with backoff. Bounded per reconcile (50), like `maxValidationsPerTick`.
5. **Resume check**: a volume whose `manifest.json` already exists in S3 with the same `pipeline_sha256` → `D` without a Job (today's "done detection"). One HEAD per pending volume, once; result cached in status.
6. **Submit**: while running < `spec.window` and global in-flight < controller cap: create a Job for the next `P` volume — the `jobspec.py` port: labels `batch.htrflow/*`, `kueue.x-k8s.io/queue-name: <namespace LocalQueue>`, `backoffLimit: 0`, `podFailurePolicy` (exit 13 → FailJob, 143 → Ignore), `activeDeadlineSeconds = max(min, pages × perPage)`, `ttlSecondsAfterFinished`, ownerReference → the CR, env contract unchanged. Fairness across CRs in a namespace: round-robin by CR creation time (today's planner).
7. **Job events** (watch owned Jobs): Succeeded → `D`; Failed with permanent reason → `F:n` final; Failed transient with attempts < `maxAttempts` → back to `P` (attempt counter in the code); ≥ max → `F:n`. Append to `failures` (capped), copy the wrapper's termination message as `reason`.
8. Recompute `counts`, `phase`; `Succeeded` when pending+running = 0 and failed = 0; `Failed` when pending+running = 0 and failed > 0.
9. Metrics: `htrflow_volumes_total{namespace,pipeline,outcome}`, `htrflow_pages_total{…}` (from `manifest.json` page count), `htrflow_volume_seconds` histogram, `htrflow_jobs_inflight` gauge.

**Pipeline reconcile**: verify digest form, optional model revision rule, render
steps → sha256 → status; fan out ConfigMaps to namespaces with referencing jobs.

**Dropped from the reconciler**: Git clone, Lease, tick deadline, `status.json`,
`attempts.json`, `validation.json`, `volumes.json`, thumbnails,
`metrics-failed-latest.json`, grandfathering guards, orphan accounting (a
result prefix with no CR is simply not shown; `kubectl` is the inventory).

## 5. Read API for the status page (D8)

`GET /api/v1/jobs` → `[{namespace, name, pipeline, phase, counts, createdAt, resultsBase}]`;
`GET /api/v1/jobs/{ns}/{name}` → the CR status plus, per volume, the S3 links the
page needs (`manifest.json`, `iiif.json`, viewer URL, run log key) derived from
`resultsBase` by convention — no S3 calls in the controller. Read-only, no
auth (internal, same network position as today's `status.json`), served from
the controller pod on `:8081`; the viewer nginx proxies `/api/`. The frontend's
`status.ts` derivation layer is replaced by this shape; `CampaignCard` and
`PagesTable` keep their props.

## 6. Chart and packaging

- `charts/htrflow-batch`: `crds/` (generated), `controller.yaml` (Deployment,
  SA, Role/ClusterRole for Jobs/ConfigMaps/CRDs/Leases, PDB), NetworkPolicy for
  the controller (API server + S3 HEAD + IIIF sources egress), values
  `controller.{image, window, maxInflight, validationsPerReconcile, perPageSeconds, minDeadlineSeconds}`.
  Removed: `reconciler.yaml`, `job-example.yaml`, `devstack-*.yaml`, `pipelines.yaml`
  (pipelines are CRs now), `exampleJob`, `reconciler.*`, `devStack.*` values.
- `charts/htrflow-devstack`: the four devstack templates, unchanged, own values.
- Image `docker.io/riksarkivet/htrflow-controller` built by the same publish
  matrix (distroless, non-root, digest-pinned), replacing `htrflow-reconciler`.
- `scripts/campaigns/convert.py`: `campaigns/*.yaml` + `pipelines/*.yaml` →
  `Pipeline` and `TranscriptionJob` manifests (splitting at 10 000 volumes).

## 7. Wrapper and frontend changes

- Wrapper: remove `make_thumbnail`/`previous_thumbnail` and
  `publish_failure_metrics`; nothing else. Env/exit contract untouched.
- Frontend: fetch `/api/v1/jobs` instead of `status.json`; delete `status.ts`
  derivation of campaigns/attempts/validation; keep components. The STALE banner
  becomes "controller unreachable" (endpoint down) instead of a tick-age check.

## 8. Testing

- Controller unit tests + **envtest** (as rask-operator): spec validation,
  window/fairness, Job outcome → status transitions incl. permanent vs
  transient exits, pause/delete cleanup, resume-from-S3 (fake S3 HEAD).
- Contract test (B21): the wrapper's `manifest.json` fixture ↔ controller's
  reader; the `/api/v1/jobs` fixture ↔ frontend tests.
- Chart: `helm template` + kubeconform incl. CRDs; `ci/full-values.yaml` updated.
- E2E on the PoC node (the only cluster with the wrapper today): `make
  campaigns-apply` with 50 volumes → all `D`, viewer opens, no `status/*.json`
  written; pause mid-run leaves done pages; delete GCs Jobs.

## 9. Rollout

1. Build behind the new chart version (0.3.0); PoC runs the controller; the
   CronJob reconciler is deleted from the PoC namespace after the E2E passes.
2. Existing results in S3 need no migration (`manifest.json` is unchanged);
   `resume check` (4.5) recognises them.
3. I15 deploys the controller chart in dev; the I15 design's "reconciler
   CronJob" and `campaignsRepoUrl` values are replaced by Argo applying the
   campaigns repo's CR manifests.
4. Docs: how-it-works/campaigns and reconciler pages rewritten around the CRs;
   `docs/reference/campaign-yaml.md` becomes the CRD reference.

## 10. Open points (answer at review)

- **D1 language**: Go (recommended) or Python/kopf?
- Global in-flight cap default (today `window: 20` per system) — keep 20 as the
  per-CR default and 40 as the global cap?
- Should `Pipeline` also carry the CER/WER fields now (T12) or stay minimal?
