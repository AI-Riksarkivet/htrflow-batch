# Indexed Jobs E2E on the PoC

The B63 acceptance run: chart 0.3.0 and the converter driving real campaigns
on the single-node GB10 k3s PoC, with the CronJob reconciler gone. Every
command and every observation below is from that run
(2026-09-01, 15:36–16:23 local time / 13:36–14:23 UTC).

This is a **record of one run**, not a runbook — the day-to-day loop is
[Local k3s](local-k3s.md). The reproducible core (validate → render → apply →
wait for `completedIndexes`) is `make e2e DIR=<campaigns-repo>`; the failure
paths are campaigns plus `kubectl`, written out below.

## What ran

| | |
|---|---|
| Node | GB10 (arm64, 1 × NVIDIA GB10), k3s **v1.35.5+k3s1** (client and server) |
| Kueue | ClusterQueue `htr-batch-cq`, LocalQueue `htr-batch`, `nominalQuota` cpu 8 / memory 32Gi / **nvidia.com/gpu 1** (unchanged by this run) |
| Helm | `htr` → `htrflow-batch-0.3.0` (rev 27), `htr-devstack` → `htrflow-devstack-0.1.0` (rev 3) |
| Wrapper | `htrflow-batch-wrapper` **0.2.0**, built natively from `.docker/htrflow-batch-gpu-arm64.dockerfile` on `htrflow:v0.2.6-arm64` (`HTRFLOW_BASE_REVISION=v0.2.6-78-gf7de861`) |
| Campaigns repo | `~/htr-test`, branch `b63-indexed` (never pushed) |

`backoffLimitPerIndex` needs Kubernetes ≥ 1.29; 1.35.5 has it GA.

### Images (local registry only)

Built with `make poc-push-arm64 IMAGE_TAG=e2e` and `make viewer-image
IMAGE_TAG=e2e` + `docker push`. These digests exist **only** in the
in-cluster PoC registry `127.0.0.1:30500`; they are deliberately not in the
chart defaults.

```
wrapper: 127.0.0.1:30500/htrflow-batch@sha256:056478e9843683cf637fd7b0a625aeef86a37cd2eaf2b520d6c68a234a866aa9
api:     127.0.0.1:30500/htrflow-api@sha256:a97a82b08ef0ec38f2075124e2dd2b61bc73ffbf0f9d297022c60c43a5bc9d2b
viewer:  127.0.0.1:30500/uv4@sha256:94ae2ddfb75a98d6640560f44156199c433cdf989a0b893eb68d954553814050
```

Nothing was published to Docker Hub: the node is arm64 and `publish.yml`
is the maintainer's merge-time action.

## Step 1 — preconditions

```console
$ kubectl version
Client Version: v1.35.5+k3s1
Server Version: v1.35.5+k3s1

$ kubectl -n htr-batch delete cronjob htr-reconciler
cronjob.batch "htr-reconciler" deleted from htr-batch namespace
```

Deleting the CronJob cascade-deleted its `htr-reconciler-*` Jobs. The five
pre-B63 `htr-warmup-*` Jobs (rendered by the old reconciler) were deleted by
hand; their marker files on the model-cache PVC were left alone. No PVC,
Secret, RustFS object or bucket key was deleted at any point.

## Step 2 — build, not publish

`make poc-push-arm64` failed first:

```
ERROR: failed to build: ... "/packages/reconciler/pyproject.toml": not found
```

All three dockerfiles still bind-mounted the removed `packages/reconciler`
into the uv workspace. Fixed in
`fix(docker): bind every workspace member's pyproject; packages/reconciler is
gone`, with `packages/wrapper/tests/test_dockerfile_workspace.py` as the gate
(nothing in CI builds these dockerfiles — dagger builds its own graph — so
without a test this breaks again on the next workspace change).

`packages/wrapper/pyproject.toml` went to `0.2.0` and `uv lock` followed.
Verified in the image:

```console
$ docker run --rm --entrypoint /app/.venv/bin/python 127.0.0.1:30500/htrflow-batch:e2e \
    -c "import importlib.metadata as m; print(m.version('htrflow-batch-wrapper'))"
0.2.0
```

**Deviation — the viewer image.** `make viewer-image` needs a built
`universalviewer4` checkout (`UV4_DIR`), which no longer exists on this node.
The UV half of the image was recovered from the previously published
`127.0.0.1:30500/uv4:dev13` (`docker create` + `docker cp
/usr/share/nginx/html`), the SvelteKit files of that older SPA were removed
from it, and `make viewer-image UV4_DIR=<that dir>` built the image with a
freshly built `frontend/dist`. The nginx layer, the `/api/` proxy and the SPA
are therefore current; the UV4 assets are the same bytes as the previous PoC
viewer. `dagger call build-viewer` (which clones UV4 itself) is the
reproducible path and was not used here.

## Step 3 — install

`charts/htrflow-devstack` is a new release that has to **adopt** the RustFS,
registry and device-plugin objects the 0.2.0 `htr` release (or a hand-apply)
owned, or the `htr` upgrade to 0.3.0 would delete them. Each object got
`meta.helm.sh/release-name=htr-devstack`,
`meta.helm.sh/release-namespace=htr-batch`,
`app.kubernetes.io/managed-by=Helm` and — for the ones the old chart owned —
`helm.sh/resource-policy=keep`, which is what makes the `htr` upgrade skip
them (Helm re-reads the annotation from the **live** object before deleting).

> **Trap, paid for once.** The first `make install-devstack` failed on a
> server-side-apply conflict (`kubectl-client-side-apply` owned the
> hand-applied DaemonSet's `image`). Re-running with
> `nvidiaDevicePlugin.enabled=false` "succeeded" — and deleted the `nvidia`
> RuntimeClass and the device-plugin DaemonSet, because revision 1 had
> already claimed them and revision 2 no longer rendered them. `nvidia.com/gpu`
> allocatable went to 0 and the first campaign pod was rejected with
> `RuntimeClass "nvidia" not found`. Re-running `make install-devstack` (plugin
> enabled) recreated both from the chart and restored `nvidia.com/gpu: 1`.
> **A failed Helm install still owns what it applied** — do not follow it with
> a narrower one.

```console
$ helm upgrade htr charts/htrflow-batch -n htr-batch -f poc-values.yaml
STATUS: deployed        REVISION: 27
$ make psa-labels           # enforce=baseline, warn/audit=restricted

$ kubectl -n htr-batch get deploy htrflow-api
NAME          READY   UP-TO-DATE   AVAILABLE
htrflow-api   1/1     1            1

$ curl -s http://localhost:30800/api/v1/jobs
[]
```

The orphaned `git-daemon` Deployment and Service (no consumer since B63) were
deleted by hand — the 0.3.0 charts render neither.

The PoC values file (not committed; local digests):

```yaml
publicResultsBase: "http://localhost:30900/htr-results"
# plus the pre-B63 layout flag, set true — retired in Task 15 below
modelCache: { create: false, name: htr-test-data }
queue:
  name: htr-batch
  flavor: default-flavor
  resources: [{name: cpu, quota: 8}, {name: memory, quota: 32Gi}, {name: nvidia.com/gpu, quota: 1}]
api:    { image: 127.0.0.1:30500/htrflow-api@sha256:a97a82b0… }
viewer: { enabled: true, image: 127.0.0.1:30500/uv4@sha256:94ae2ddf…, nodePort: 30800 }
security: { allowedImageRepos: ["127.0.0.1:30500/"], psaEnforce: baseline, allowTagImages: false }
network:
  enabled: true
  defaultDeny: true
  iiifCidrs: ["192.121.221.27/32", "0.0.0.0/0"]   # the htr_demo pages live on huggingface.co
```

`network.apiServer.cidr` was left empty on purpose: the chart's `lookup` of
`Endpoints default/kubernetes` resolved it (10.16.51.56/32) and the read API's
NetworkPolicy came out right — the API answers, so its egress to the
apiserver works.

## Step 4 — campaigns

`~/htr-test` was converted from the pre-B63 layout (`campaigns/`, `pipelines/`,
no `converter.yaml`) on a new branch `b63-indexed`. `converter.yaml` carries
the PoC values — `namespace: htr-batch`, `queue: htr-batch`, `window: 1`
(one GPU), `data_pvc: htr-test-data`, `s3_secret: htr-batch-s3`,
`runtime_class: nvidia`, `public_results_base:
http://localhost:30900/htr-results`, the pre-B63 layout flag set true,
`allowed_image_repos: ["127.0.0.1:30500/"]`. One pipeline (`e2e-v1`) pinned to
the wrapper digest above; everything else deleted.

```console
$ uv run htrflow-campaigns validate ~/htr-test     # exit 0
$ make campaigns-apply DIR=/home/morgan/htr-test
```

### The first apply was rejected by Kueue

```
Error from server (Forbidden): admission webhook "vjob.kb.io" denied the request:
metadata.annotations[kueue.x-k8s.io/job-min-parallelism]: Invalid value: 1:
should be between 0 and 0
```

The converter set `job-min-parallelism: "1"` unconditionally, but Kueue
validates it against `[0, parallelism-1]` — so **every `window: 1` campaign was
un-submittable**, which is the shape a one-GPU cluster renders by default.
Fixed in `fix(converter): job-min-parallelism only above parallelism 1`
(with a test): partial admission has nothing to do below 2 anyway.

### Warm-up

```console
$ kubectl -n htr-batch get job htr-warmup-e2e-v1
NAME                STATUS     COMPLETIONS   DURATION
htr-warmup-e2e-v1   Complete   1/1           13s
```

13 s because the model cache PVC already held the three models; the marker it
writes (`/data/warmup/e2e-v1.done`) is what every campaign pod's init
container waits for.

### Indexes progressing on one GPU

`campaigns/e2e-demo.yaml`, ten one-page volumes, `parallelism: 1`:

```console
$ kubectl -n htr-batch get job e2e-demo -o jsonpath='{.status.completedIndexes}'
0-2 … 0-5 … 0-7 … 0-9
$ kubectl -n htr-batch get job e2e-demo
NAME       STATUS     COMPLETIONS   DURATION
e2e-demo   Complete   10/10         2m8s
```

`campaigns/e2e-50.yaml` is the scale assertion — fifty one-page volumes, one
index at a time on one GPU. The four htr_demo example pages are cycled across
fifty volume ids: what is being asserted is Job/index bookkeeping and
throughput, not corpus coverage. Measured ~22 s per volume end to end (pod
start, model load off the read-only cache, one page, S3 publish), so ~18
minutes wall clock.

```console
$ kubectl -n htr-batch get job e2e-50
NAME     STATUS     COMPLETIONS   DURATION
e2e-50   Complete   50/50         17m

$ kubectl -n htr-batch get job e2e-50 -o jsonpath='{.status.completedIndexes}'
0-49
```

A campaign is append-only, so the fifty volumes are a **new file** rather than
fifty more volumes in `e2e-demo.yaml` — `render` refuses the latter.

### Results, viewer and read API

```console
$ curl -s http://localhost:30800/ | head -1
<!doctype html>                       # the campaign browser, not UV's demo page
$ curl -s -o /dev/null -w '%{http_code}' http://localhost:30800/uv.html
200
$ curl -s -o /dev/null -w '%{http_code}' \
    http://localhost:30900/htr-results/e2e-v1/e2e-demo-01/manifest.json
200
```

`GET /api/v1/jobs/htr-batch/e2e-badurl` returns per-volume rows straight off
the Job — `state`, `manifestUrl`, `iiifUrl`, `altoPrefix`, `logUrl`, and for
the failed index the pod's termination message as `reason`:

```json
{ "index": 0, "id": "e2e-bad-manifest", "state": "failed",
  "logUrl": "http://localhost:30900/htr-results/status/logs/e2e-v1/e2e-bad-manifest.txt",
  "reason": "{\"stage\": \"setup\", \"permanent\": true, \"error\": \"manifest fetch failed: …: HTTP 404\"}" }
```

### No status files

Nothing under `status/` was written by the new system. The three reconciler
documents are still on the bucket from its last tick at 13:30:02 UTC — six
minutes *before* the CronJob was deleted and eighteen before the first
campaign ran:

```console
$ # listing needs credentials (anonymous listing is denied by the bucket policy)
2026-09-01T13:30:02.932Z status/attempts.json
2026-09-01T13:30:02.939Z status/volumes.json
2026-09-01T13:30:02.945Z status/status.json
2026-09-01T13:5x…       status/logs/e2e-v1/<volume>.txt      # the wrapper's own run logs
```

The only `status.json` in the whole bucket is that one stale object. Every
key the run produced is either `e2e-v1/<volume>/…`,
`sources/e2e-v1/<volume>/manifest.json` (synthetic manifests for `images:`
volumes) or `status/logs/e2e-v1/<volume>.txt`.

## Step 5 — failure paths

### A bad manifest URL fails one index, not the campaign

`campaigns/e2e-badurl.yaml`: one volume pointing at a URL that 404s, two good
ones.

```console
$ kubectl -n htr-batch get job e2e-badurl -o jsonpath='{.status}'
{"completedIndexes":"1,2","failed":1,"failedIndexes":"0","succeeded":2, …}

$ kubectl -n htr-batch get pods -l batch.kubernetes.io/job-name=e2e-badurl
0 Failed    exit 13  {"stage":"setup","permanent":true,"error":"manifest fetch failed: …: HTTP 404"}
1 Succeeded exit 0
2 Succeeded exit 0
```

One pod for index 0, not four: exit 13 → `podFailurePolicy` `FailIndex` → no
retry. The other two indexes ran and published normally.

Note the Job's own condition is `Failed`/`FailedIndexes` even though
`maxFailedIndexes` (3) was not exceeded: with `backoffLimitPerIndex` set,
Kubernetes fails the Job once every index is terminal and at least one
failed. The read API reports the campaign as `phase: "Failed"` with
`counts: {total: 3, done: 2, failed: 1}` — which is the honest summary, but
worth knowing before reading "Failed" as "nothing came out".

### `MAX_SECONDS` kills an attempt; the retry resumes

`MAX_SECONDS` is a `converter.yaml` global, so the probe lives in a nested
mini-repo `probe-max-seconds/` with its own `converter.yaml`
(`max_seconds: 60`) — `htrflow-campaigns` only ever reads
`<dir>/converter.yaml`, `<dir>/campaigns`, `<dir>/pipelines`, so the directory
is invisible to a render of the repo root.

The first probe (four pages) **completed in 27 s** and never hit the watchdog —
this GPU is faster than the 60 s budget for anything small. A sixty-page
volume did:

```console
$ kubectl -n htr-batch get pods -l batch.kubernetes.io/job-name=e2e-timeout-60p
e2e-timeout-60p-0-ckwwh 14:00:06Z Failed    exit 1  {"stage":"stream","permanent":false,"error":"MAX_SECONDS"}
e2e-timeout-60p-0-w62wx 14:01:21Z Failed    exit 1  {"stage":"stream","permanent":false,"error":"MAX_SECONDS"}
e2e-timeout-60p-0-4bwk6 14:02:45Z Succeeded exit 0

$ kubectl -n htr-batch get job e2e-timeout-60p -o jsonpath='{.status}'
{"completedIndexes":"0","failed":2,"failedIndexes":"","succeeded":1, …}
```

Exit 1 is transient, so Kubernetes retried the index under
`backoffLimitPerIndex: 3`, and **each retry resumed from the pages already
published** — which is why the third attempt finished. All sixty pages are on
the bucket exactly once:

```console
e2e-v1/e2e-timeout-60p/  → alto: 60, page: 60, iiif.json, manifest.json, pipeline.yaml
```

### The retry cap

`campaigns/e2e-retrycap.yaml` points at a host that does not resolve, so every
attempt raises `TransientManifestError` → exit 1 → retry, and the index runs
out of budget:

```console
$ kubectl -n htr-batch get job e2e-retrycap -o jsonpath='{.status}'
{"failed":4,"failedIndexes":"0","conditions":[… {"type":"Failed","reason":"FailedIndexes"}]}

$ kubectl -n htr-batch get pods -l batch.kubernetes.io/job-name=e2e-retrycap
e2e-retrycap-0-nlh2k 14:21:08Z Failed exit 1 {"stage":"setup","permanent":false,"error":"manifest fetch failed: http://unreachable.invalid/manifest.json: ConnectError: [Errno -2] N…"}
e2e-retrycap-0-f72bl 14:21:22Z Failed exit 1  (same)
e2e-retrycap-0-6nlkb 14:21:44Z Failed exit 1  (same)
e2e-retrycap-0-2gzm8 14:22:27Z Failed exit 1  (same)
# 1 attempt + backoffLimitPerIndex: 3 retries = 4 pods, then the index is capped
```

### Pause and resume — `suspend: true` on the Job does NOT hold

The documented rule is "pausing a campaign is a Git change: `suspend: true`
on the rendered Job". Applied mid-run against a **Kueue-managed** Job, that is
not what happens:

```console
$ # rendered/campaigns/e2e-demo.yaml, spec.suspend: true, applied at 15:54:16
$ kubectl apply -f ~/htr-test/rendered/campaigns/e2e-demo.yaml
job.batch/e2e-demo configured

$ kubectl -n htr-batch get events --sort-by=.lastTimestamp | grep e2e-demo
22s  Normal  SuccessfulDelete  job/e2e-demo  Deleted pod: e2e-demo-3-w72pc
22s  Normal  Suspended         job/e2e-demo  Job suspended
20s  Normal  SuccessfulCreate  job/e2e-demo  Created pod: e2e-demo-3-g9tw4     ← two seconds later

$ kubectl -n htr-batch get job e2e-demo -o jsonpath='{.spec.suspend}'
false
```

Kubernetes did its half correctly — the in-flight pod for index 3 was deleted
and the three finished indexes were kept — but the Workload was still
admitted, so **Kueue's reconciler resumed the Job two seconds later**. The
pause did not stick.

What does pause a single admitted campaign is deactivating its Workload:

```console
$ kubectl -n htr-batch patch workload job-e2e-demo-224ca --type=merge -p '{"spec":{"active":false}}'

$ kubectl -n htr-batch get job e2e-demo -o jsonpath='suspend={.spec.suspend} completed={.status.completedIndexes} active={.status.active}'
suspend=true completed=0-3 active=

$ kubectl -n htr-batch get workload job-e2e-demo-224ca -o jsonpath='{.status.conditions}'
QuotaReserved False Pending      The workload is deactivated
Evicted       True  Deactivated  The workload is deactivated
Admitted      False NoReservation

$ curl -s http://localhost:30800/api/v1/jobs | jq '.[] | select(.name=="e2e-demo")'
{ "phase": "Paused", "counts": {"total":10,"active":0,"done":4,"failed":0}, "suspended": true }
```

Active pods gone, four finished indexes kept, and the read API's `Paused`
phase is exactly right. Reactivating resumes at index 4:

```console
$ kubectl -n htr-batch patch workload job-e2e-demo-224ca --type=merge -p '{"spec":{"active":true}}'
$ kubectl -n htr-batch get pods -l batch.kubernetes.io/job-name=e2e-demo | tail -1
e2e-demo-4-jqcnq   1/1   Running   0   5s
```

**This was a design gap, not a bug fixed in this round.** It is closed in
[Fix round 1](#fix-round-1): `suspend:` stays the declared
intent in Git, and `make campaigns-apply` (or an Argo CD `PostSync` hook) runs
`scripts/kueue-pause-sync.sh`, which applies that intent to the Workload's
`spec.active`.

### Kueue partial admission

`campaigns/e2e-window.yaml` rendered `parallelism: 4` against
`nominalQuota: 1` GPU (the queue's quota was read first and not changed):

```console
$ kubectl -n htr-batch get job e2e-window -o jsonpath='parallelism={.spec.parallelism} completions={.spec.completions}'
parallelism=1 completions=4                      # Kueue rewrote 4 → 1

$ kubectl -n htr-batch get workload job-e2e-window-fb7db -o json | …
podSets:   [('main', count=4, minCount=1)]
admission: {"clusterQueue":"htr-batch-cq",
            "podSetAssignments":[{"name":"main","count":1,
              "resourceUsage":{"cpu":"4","memory":"8Gi","nvidia.com/gpu":"1"}}]}

$ kubectl -n htr-batch get pods -l batch.kubernetes.io/job-name=e2e-window
e2e-window-0-k984t   Completed
e2e-window-1-r9c6g   Running          # one at a time, never four

$ kubectl -n htr-batch get job e2e-window
NAME         STATUS     COMPLETIONS   DURATION
e2e-window   Complete   4/4           82s
```

Admitted with 1 instead of left pending — which is what
`kueue.x-k8s.io/job-min-parallelism` buys.

### Re-applying a partially-admitted campaign is rejected

The other half of that coin, and a real operational trap:

```
Error from server (Forbidden): admission webhook "vjob.kb.io" denied the request:
spec.parallelism: Forbidden: cannot change when partial admission is enabled
and the job is not suspended
```

`rendered/` says `parallelism: 4`; the live Job says `1` because Kueue
rewrote it; so `kubectl apply` (and Argo CD's sync) of that unchanged file
fails for as long as the Job exists — including after it has completed, until
`ttlSecondsAfterFinished` (24 h) removes it. `make campaigns-apply` is
therefore **not** idempotent for any campaign whose `window` exceeds the
queue's quota, contrary to what [Local k3s](local-k3s.md) says. Worked around
here by deleting the finished `e2e-window` Job; **fixed** in
[Fix round 1](#fix-round-1) by dropping partial admission
and clamping `parallelism` to `converter.yaml`'s `window` at render time.

### Deleting a campaign file cancels it

Two things were missing for this to be true, both fixed in
`fix(converter): label ConfigMaps and drop stale rendered files so a deleted
campaign is really cancelled`:

1. `render` never removed a deleted campaign's file from `--out`, so the Job
   kept being re-applied (and the campaigns repo's `render.yml` only does
   `git add rendered`, which cannot stage a deletion that never happened).
   `render` now deletes files under `--out` that this render did not produce.
2. The two ConfigMaps carried no labels, so a label-selector prune deleted the
   Job and left the `volumes.txt` ConfigMap behind. Every rendered object now
   carries `htrflow.riksarkivet.se/managed-by=converter`.

Pruning is opt-in on the PoC (`make campaigns-apply PRUNE=1` →
`kubectl apply --prune -l htrflow.riksarkivet.se/managed-by=converter`), because
`--prune` deletes every converter-labelled object in the namespace that is not
in *this* apply — running it against a partial checkout such as
`probe-max-seconds/` would cancel everything else. It is opt-in with Argo CD
too: `syncPolicy.automated.prune` defaults to `false`, so an Application that
manages a campaigns repo needs `prune: true` (or a `--prune` sync) or a
deleted campaign's Job is left running.

The label selector is also what keeps the prune from touching anything the
converter did not render: the twenty pre-B63 `htr-pipeline-*` ConfigMaps
(created by chart 0.2.0, unlabelled) survived it untouched.

```console
$ git -C ~/htr-test rm campaigns/e2e-demo.yaml
$ make campaigns-apply DIR=/home/morgan/htr-test PRUNE=1
configmap/htr-pipeline-e2e-v1 unchanged
job.batch/htr-warmup-e2e-v1 configured
configmap/campaign-e2e-50 unchanged
job.batch/e2e-50 configured
…
configmap/campaign-e2e-demo pruned
job.batch/e2e-demo pruned

$ # its fifty published objects are still there, untouched
e2e-v1/e2e-demo-* objects BEFORE prune: 50
e2e-v1/e2e-demo-* objects AFTER  prune: 50

$ curl -s http://localhost:30800/api/v1/jobs | jq -r '.[].name'
e2e-retrycap
e2e-50
e2e-badurl
```

## Bugs found and fixed during the run

| Commit | What was broken |
|---|---|
| `fix(docker): bind every workspace member's pyproject…` | all three image builds failed on the removed `packages/reconciler` |
| `fix(converter): job-min-parallelism only above parallelism 1…` | Kueue's webhook rejected every `parallelism: 1` campaign — the default shape on a one-GPU cluster |
| `fix(converter): label ConfigMaps and drop stale rendered files…` | "deleting a campaign file cancels it" was not achievable: stale `rendered/` files and unlabelled ConfigMaps |

## Open questions for the design

!!! note "All three were ruled on and closed the same day"

    See [Fix round 1](#fix-round-1) at the end of this
    page for what was implemented and re-verified. The text below is left as
    written — it is the record of what the system did before the fix.

1. **Pause needs a Kueue-shaped lever.** `suspend: true` on a Job that Kueue
   has admitted is undone by Kueue within seconds (evidence above). The
   working lever is `spec.active: false` on the *Workload*, which is not a
   file in the campaigns repo. Either the docs stop promising a Git-change
   pause, or the converter grows something that survives a render (a
   `paused: true` campaign field that renders the Job with `suspend: true`
   *and* keeps it out of the queue, or a per-campaign LocalQueue whose
   `stopPolicy` can be set).
2. **`parallelism` and partial admission fight over the same field.** A
   campaign whose `window` exceeds the queue quota cannot be re-applied while
   its Job exists. Argo CD would report it as permanently OutOfSync. Options:
   render `parallelism` equal to the queue's realistic capacity, drop
   `job-min-parallelism` and let campaigns queue, or teach the apply path to
   skip Jobs that already exist.
3. **`MAX_SECONDS` is a `converter.yaml` global.** Sizing it for the slowest
   volume in the repo makes it useless for the fastest; a per-pipeline (or
   per-campaign) override would be the natural place.

## Cluster state left behind

- `htr` = `htrflow-batch-0.3.0` (rev 27), `htr-devstack` =
  `htrflow-devstack-0.1.0` (rev 3), both in `htr-batch`.
- The `nvidia` RuntimeClass and the kube-system device-plugin DaemonSet are
  now **Helm-managed** by `htr-devstack` (they were hand-applied before).
  RustFS, the registry, both PVCs and the S3 Secret were adopted into
  `htr-devstack` and all carry `helm.sh/resource-policy: keep`.
- Gone: the `htr-reconciler` CronJob and its Jobs, the five pre-B63
  `htr-warmup-*` Jobs, the `git-daemon` Deployment and Service.
- Campaign Jobs left in the namespace expire on their own
  (`ttlSecondsAfterFinished: 86400`). Nothing else needs cleaning up.
- The bucket keeps everything: pre-B63 results, the three stale reconciler
  status documents, and this run's `e2e-v1/…` output.
- `~/htr-test` branch `b63-indexed` is committed and **not pushed**; its
  pipeline pins a `127.0.0.1:30500` digest that resolves on this node only.

---

# Fix round 1

*Same day, 16:34–16:40.* Three of the open questions above came back as rulings and were implemented
and re-verified on the same cluster, same images. The campaigns repo grew a
`suspend:` field on `campaigns/e2e-pause.yaml`, a `max_seconds:` pipeline
(`pipelines/e2e-slow-v1.yaml`) and a re-added `campaigns/e2e-window.yaml`;
`probe-max-seconds/` is gone — the override it needed is a pipeline field now.

## Pause is declared in Git and enforced at apply time

`suspend: true` on a campaign renders `spec.suspend: true` (new `Campaign`
field). Because Kueue owns that field for an admitted Workload, the apply
step now also puts the intent on the Workload:
`scripts/kueue-pause-sync.sh <ns> <rendered/campaigns>`, run by
`make campaigns-apply` after every apply (Argo CD: the same script as a
`PostSync` hook — manifest in
[Campaign & Pipeline YAML → Pausing](../reference/campaign-yaml.md#pausing)).

```console
$ # campaigns/e2e-pause.yaml, 8 volumes, running with 3 done: add `suspend: true`
$ kubectl -n htr-batch get job e2e-pause -o jsonpath='…'
BEFORE suspend=false done=0-2 active=1

$ make campaigns-apply DIR=/home/morgan/htr-test
job.batch/e2e-pause configured
scripts/kueue-pause-sync.sh htr-batch /home/morgan/htr-test/rendered/campaigns
e2e-pause: job-e2e-pause-5b54e active=false
workload.kueue.x-k8s.io/job-e2e-pause-5b54e patched

$ kubectl -n htr-batch get job e2e-pause -o jsonpath='…'
AFTER  suspend=true done=0-2 active= ready=0
$ kubectl -n htr-batch get pods -l batch.kubernetes.io/job-name=e2e-pause
e2e-pause-0-9d2rj  Completed
e2e-pause-1-n28hx  Completed
e2e-pause-2-v8qdk  Completed        # no running pod
$ curl -s http://localhost:30800/api/v1/jobs | jq '.[]|select(.name=="e2e-pause")'
{ "phase": "Paused", "counts": {"total":8,"active":0,"done":3,"failed":0}, "suspended": true }
```

It **held** — 65 s later `suspend` was still `true` (in round 0, a bare
`suspend: true` was undone by Kueue in 2 s), and a second
`make campaigns-apply` while paused printed nothing from the sync script (it
skips a Workload already in the wanted state) and left the campaign paused.

`suspend: false` + apply resumes at the next index, not the first:

```console
$ make campaigns-apply DIR=/home/morgan/htr-test
e2e-pause: job-e2e-pause-5b54e active=true
workload.kueue.x-k8s.io/job-e2e-pause-5b54e patched

$ kubectl -n htr-batch get job e2e-pause -o jsonpath='…'
RESUMED suspend=false done=0-2 active=1
$ kubectl -n htr-batch get pods -l batch.kubernetes.io/job-name=e2e-pause | tail -1
e2e-pause-3-cxbt6   1/1   Running   0   7s          # index 3, not index 0

$ kubectl -n htr-batch get job e2e-pause
NAME        STATUS     COMPLETIONS   DURATION
e2e-pause   Complete   8/8           105s
```

## Partial admission is gone; `converter.yaml: window` is the cap

The `kueue.x-k8s.io/job-min-parallelism` annotation is no longer rendered at
all, and `parallelism` is `min(campaign window, converter window)`. The
campaign that used to trigger the trap now renders what it runs:

```console
$ # campaigns/e2e-window.yaml declares `window: 4`; converter.yaml caps at 1
$ kubectl -n htr-batch get job e2e-window -o jsonpath='…'
parallelism=1 completions=4     # clamped at render time, no annotation on the Job

$ kubectl -n htr-batch get job e2e-window
NAME         STATUS     COMPLETIONS   DURATION
e2e-window   Complete   4/4           69s
```

Re-applying the unchanged rendered files is now clean in every state — the
webhook error from round 0 is gone:

```console
$ # with e2e-maxseconds RUNNING and e2e-50/e2e-badurl/e2e-retrycap terminal
$ make campaigns-apply DIR=/home/morgan/htr-test
job.batch/e2e-50 configured
job.batch/e2e-badurl configured
job.batch/e2e-maxseconds configured
job.batch/e2e-pause configured
job.batch/e2e-retrycap configured
job.batch/e2e-window configured          # exit 0, no vjob.kb.io rejection

$ # and again with all eight Jobs terminal: 16 objects configured/unchanged, exit 0
```

## Per-pipeline `max_seconds`

`pipelines/<id>.yaml` may set `max_seconds:`; it overrides `converter.yaml`'s
global for that recipe's campaigns. The nested `probe-max-seconds/` mini-repo
is deleted — `pipelines/e2e-slow-v1.yaml` (`max_seconds: 60`) lives in the
main repo beside `e2e-v1`:

```console
$ kubectl -n htr-batch get pods -l batch.kubernetes.io/job-name=e2e-maxseconds \
    -o jsonpath='{.items[*].spec.containers[0].env[?(@.name=="MAX_SECONDS")].value}'
60                       # pipeline e2e-slow-v1

$ kubectl -n htr-batch get pod e2e-50-0-5h7c5 \
    -o jsonpath='{.spec.containers[0].env[?(@.name=="MAX_SECONDS")].value}'
21600                    # pipeline e2e-v1, converter.yaml's global
```

The 20-page probe volume finished inside its 60 s budget on the first attempt
(~2.5 s/page once the models are warm), so the watchdog did not fire this
time; that it fires and that the retry resumes is the 60-page evidence in the
round-0 section above. What this run proves is the **override reaching the
pod**.

## What is still open

Open question 1 (pause) and 2 (`parallelism`) above are closed by this round;
3 (`max_seconds` scope) is closed by the pipeline field. The round-0 text is
left as written — it is the record of what the system did before the fix.

---

# Fix round 2

*Same node, same images, same campaigns repo branch.* Five review findings
from fix round 1 were fixed; one of them — **pause on create** — was a real
hole and is the only one with new cluster evidence, below. The others are
code and documentation: `render` refusing an `--out` that contains its own
sources and printing every deletion, bool/zero guards on `window` and
`max_seconds`, the pause-intent grep made tolerant and covered by a test, and
the correction that **Argo CD does not prune by default**
(`syncPolicy.automated.prune` is `false` unless you set it).

## Pause on create — a brand-new `suspend: true` campaign ran anyway

`scripts/kueue-pause-sync.sh` skipped any campaign whose Kueue Workload did
not exist yet. That is *every campaign on the apply that creates it*, so a
campaign committed as `suspend: true` was admitted by Kueue and ran to
completion — the pause only ever worked on a campaign that had already been
applied once. The script now waits (10 × 1 s) for a paused campaign's
Workload and exits non-zero if it never appears.

`campaigns/e2e-pausenew.yaml` (3 volumes, `suspend: true`, never applied
before):

```console
$ make campaigns-apply DIR=/home/morgan/htr-test
…
configmap/campaign-e2e-pausenew created
job.batch/e2e-pausenew created
…
scripts/kueue-pause-sync.sh htr-batch /home/morgan/htr-test/rendered/campaigns
e2e-pausenew: job-e2e-pausenew-38e9e active=false
workload.kueue.x-k8s.io/job-e2e-pausenew-38e9e patched

$ kubectl -n htr-batch get job e2e-pausenew \
    -o custom-columns=SUSPEND:.spec.suspend,ACTIVE:.status.active,DONE:.status.completedIndexes
SUSPEND   ACTIVE    DONE
true      <none>    <none>

$ kubectl -n htr-batch get workload job-e2e-pausenew-38e9e -o jsonpath='{.spec.active}'
false

$ curl -s localhost:30800/api/v1/jobs/htr-batch/e2e-pausenew | jq '{phase,suspended,counts}'
{ "phase": "Queued", "suspended": true,
  "counts": { "total": 3, "active": 0, "done": 0, "failed": 0 } }
```

It **held** — still `suspend: true`, Workload `active: false`, zero pods
2 m 14 s later — and a second `make campaigns-apply` while paused issued no
patch and changed nothing. Flipping the file to `suspend: false` and applying
resumed it (`e2e-pausenew: job-e2e-pausenew-38e9e active=true`); the campaign
finished **3/3 in 69 s** (06:37:47 → 06:38:56, `completedIndexes: 0-2`).

!!! warning "Not closed: one pod still runs for ~4 s"

    Kueue creates and **admits** the Workload in the same second the Job is
    created, and unsuspends the Job before any apply-time script can look at
    it. Measured on this run: Job created 06:35:07, `Resumed` +
    `SuccessfulCreate pod: e2e-pausenew-0-4h5nf` at 06:35:07, the script's
    patch at 06:35:10, `SuccessfulDelete` 06:35:10 — the pod lived ~4 s, got
    far enough to read the manifest and start fetching the first image, and
    was SIGTERMed:

    ```console
    $ curl -s localhost:30900/htr-results/status/logs/e2e-v1/e2e-pausenew-01.txt
    06:35:11 INFO [e2e-pausenew-01] 1 pages in manifest
    06:35:11 INFO [e2e-pausenew-01] resume: 0 done, 1 to process
    06:35:11 ERROR transient failure in load: SIGTERM, shutting down
    ```

    No page, ALTO, `iiif.json` or `manifest.json` was written (404 on the
    bucket); the only trace is that 496-byte log. So the campaign does not
    *run*, but "no pod ever starts" is **not** true, and no amount of polling
    can make it true: the race is lost before the apply returns.

    The race-free levers are both **render-time** changes and therefore
    design decisions, left for a ruling: render a suspended campaign's Job
    **without** the `kueue.x-k8s.io/queue-name` label (Kueue then ignores the
    Job entirely and the rendered `spec.suspend: true` holds by itself — but
    a paused campaign leaves the queue, and the label has to come back on
    resume), or render it with `parallelism: 0` (a Job that creates no pods
    even while admitted — but a zero-count Kueue podSet may not validate).

## What is still open after round 2

Pause on create is enforced but not race-free (callout above). Everything
else from rounds 0 and 1 stays closed.

---

# Task 14 — one command, and the render-time pause experiment

*Same node, same images, same campaigns repo branch (`~/htr-test`,
`b63-indexed`).* Task 14 gave `render → apply → prune → pause` one owner,
`htrflow-campaigns apply`. Before writing its pause step, the **render-time**
alternative left open at the end of fix round 2 was measured on the cluster —
because if it worked, the apply would need no Workload logic at all.

## The experiment: can a paused campaign simply leave the queue?

The idea: render a suspended campaign's Job with `spec.suspend: true` and
**without** the `kueue.x-k8s.io/queue-name` label. Kueue then ignores the Job,
so nothing flips `suspend` back, and a campaign committed as `suspend: true`
never gets admitted at all — the ~4 s pod of fix round 2 would be gone. It
only works if **both** transitions survive Kueue's webhook in *one*
`kubectl apply`, which is all Argo CD or `apply` ever does.

`campaigns/e2e-qlabel.yaml` (8 volumes, `window: 1`) was applied unpaused and
was running index 0 when the first transition was made.

### A. running → paused (label removed + `suspend: true`): **accepted**

```console
$ kubectl -n htr-batch get job e2e-qlabel -o custom-columns=SUSPEND:.spec.suspend,ACTIVE:.status.active
SUSPEND   ACTIVE
false     1                                     # 08:21:06, pod e2e-qlabel-0-w945h Running

$ kubectl apply -f rendered/campaigns/e2e-qlabel.yaml   # label removed, suspend: true
configmap/campaign-e2e-qlabel unchanged
job.batch/e2e-qlabel configured                 # 08:21:20, exit 0

$ kubectl -n htr-batch get events --field-selector involvedObject.name=e2e-qlabel
2026-09-02T08:21:21Z   Suspended          Job suspended
2026-09-02T08:21:21Z   SuccessfulDelete   Deleted pod: e2e-qlabel-0-k8sdt
```

The Job stayed `suspend: true` with zero pods for the next 30 s+. So far so
good — **but the Workload it left behind is still admitted**:

```console
$ kubectl -n htr-batch get workload job-e2e-qlabel-ef0ef \
    -o custom-columns=ACTIVE:.spec.active,ADMITTED:.status.conditions[?(@.type=="Admitted")].status
ACTIVE   ADMITTED
true     True                                   # 08:22:50, ~90 s after the pause

$ kubectl get clusterqueue htr-batch-cq -o jsonpath='admitted={.status.admittedWorkloads} gpu_used=…'
admitted=1 pending=0 gpu_used=1                 # one GPU reserved, zero pods
```

Kueue's Job reconciler ignores a Job without the queue-name label, so it never
finalises that Workload — it is owned by the Job and outlives the pause,
holding the campaign's quota. A second campaign applied while the first was
"paused" this way was **starved**:

```console
$ kubectl apply -f rendered/campaigns/e2e-qneighbour.yaml   # 08:23:14, not paused
job.batch/e2e-qneighbour created

$ kubectl -n htr-batch get workload -o custom-columns=NAME:…,QUOTA:…,MSG:…
job-e2e-qlabel-ef0ef       true   True    Quota reserved in ClusterQueue htr-batch-cq
job-e2e-qneighbour-29337   true   False   couldn't assign flavors to pod set main:
                                          insufficient unused quota for nvidia.com/gpu
                                          in flavor default-flavor, 1 more needed
```

### B. paused → running (label added + `suspend: false`): **rejected**

```console
$ kubectl apply -f rendered/campaigns/e2e-qlabel.yaml     # 08:24:10, the unpaused render
Error from server (Forbidden): error when applying patch:
…
admission webhook "vjob.kb.io" denied the request:
metadata.labels[kueue.x-k8s.io/queue-name]: Invalid value: "htr-batch": field is immutable
```

Kueue's Job webhook treats the queue-name label as immutable unless **both**
the old and the new object are suspended, and the resuming apply un-suspends
in the same request. The brief's fallback — add the label back but leave
`suspend: true` and let Kueue unsuspend on admission — *is* accepted:

```console
$ kubectl apply -f qlabel-resume-suspended.yaml           # 08:24:28
job.batch/e2e-qlabel configured
2026-09-02T08:24:28Z   Resumed            Job resumed
2026-09-02T08:24:28Z   SuccessfulCreate   Created pod: e2e-qlabel-0-68xwj
```

…but it is not a render-time answer: the renderer would have to emit
`suspend: true` for a campaign whose file says it is *running*, and Kueue
flips that back to `false` on admission, so the very next apply of the
unchanged rendered file would pause the campaign again for a few seconds.

### Ruling

Both transitions have to pass in one apply, and B does not. Independently, A
leaks the campaign's quota for as long as it stays paused, which on a
one-GPU cluster stops everything else. **The render-time pause is rejected**:
`render` keeps writing the `kueue.x-k8s.io/queue-name` label on every
campaign, and the apply-time `Workload.spec.active` patch stays — now inside
`htrflow-campaigns apply` instead of `scripts/kueue-pause-sync.sh` (deleted).
The ~4 s window for a campaign created already paused stays open and is
documented in [the pausing reference](../reference/campaign-yaml.md#pausing).

Cleanup: `e2e-qlabel` and `e2e-qneighbour` (Jobs + ConfigMaps) deleted, quota
back to `admitted=0 pending=0 gpu_used=0`.

## `make campaigns-apply` on the PoC, now one command

`campaigns/e2e-applyrun.yaml` (2 volumes, `window: 2`, running) and
`campaigns/e2e-applypause.yaml` (2 volumes, `suspend: true`, never applied
before) were added and applied together:

```console
$ make campaigns-apply DIR=/home/morgan/htr-test           # 08:35:19
uv run htrflow-campaigns apply /home/morgan/htr-test --out /home/morgan/htr-test/rendered
kubectl apply -f /home/morgan/htr-test/rendered/pipelines
…
kubectl apply -f /home/morgan/htr-test/rendered/campaigns
configmap/campaign-e2e-applypause created
job.batch/e2e-applypause created
configmap/campaign-e2e-applyrun created
job.batch/e2e-applyrun created
…
kubectl -n htr-batch get job e2e-applypause -o 'jsonpath={.metadata.uid}'
kubectl -n htr-batch get workload -l kueue.x-k8s.io/job-uid=1bf2eaa3-… -o name
kubectl -n htr-batch get workload.kueue.x-k8s.io/job-e2e-applypause-dcce9 -o 'jsonpath={.spec.active}'
e2e-applypause: workload.kueue.x-k8s.io/job-e2e-applypause-dcce9 active=false
kubectl -n htr-batch patch workload.kueue.x-k8s.io/job-e2e-applypause-dcce9 \
  --type merge -p '{"spec": {"active": false}}'
workload.kueue.x-k8s.io/job-e2e-applypause-dcce9 patched
```

Every command the tool runs is echoed to stderr, including the read-only
lookups — that transcript *is* the audit trail an apply leaves in CI.

```console
$ kubectl -n htr-batch get job e2e-applyrun e2e-applypause \
    -o custom-columns=NAME:…,SUSPEND:.spec.suspend,ACTIVE:.status.active
NAME             SUSPEND   ACTIVE
e2e-applyrun     false     1
e2e-applypause   true      <none>

$ kubectl -n htr-batch get workload job-e2e-applypause-dcce9 job-e2e-applyrun-5f22e \
    -o custom-columns=NAME:…,ACTIVE:.spec.active,ADMITTED:…,EVICTED:…
job-e2e-applypause-dcce9   false    False      Deactivated
job-e2e-applyrun-5f22e     true     True       <none>

$ kubectl -n htr-batch get pods -l batch.kubernetes.io/job-name=e2e-applypause
No resources found in htr-batch namespace.

$ curl -s localhost:30800/api/v1/jobs        # trimmed to the two campaigns
{"name": "e2e-applypause", "phase": "Queued",  "suspended": true,
 "counts": {"total": 2, "active": 0, "done": 0, "failed": 0}}
{"name": "e2e-applyrun",   "phase": "Running", "suspended": false,
 "counts": {"total": 2, "active": 1, "done": 1, "failed": 0}}
```

The ~4 s window is **narrower but still there**: Kueue admitted the paused
campaign and created `e2e-applypause-0-2x79m` at 08:35:19, the apply's patch
landed and the pod was deleted at 08:35:20 — one second, because the pause
step now runs in-process straight after the apply instead of via a second
`make` recipe line. `e2e-applyrun` finished 2/2 at 08:36:40
(`phase: Succeeded`, `manifest.json` HTTP 200 on the bucket).

A second `make campaigns-apply` while the campaign was paused issued **no
patch** (both Workloads already agreed with git) and the pause held:
`suspend: true`, `spec.active: false`, zero pods.

### Prune still only deletes what it should

`campaigns/e2e-applypause.yaml` was deleted from the repo and the apply re-run
with `PRUNE=1`:

```console
$ make campaigns-apply DIR=/home/morgan/htr-test PRUNE=1
uv run htrflow-campaigns apply /home/morgan/htr-test --out …/rendered --prune
removed: /home/morgan/htr-test/rendered/campaigns/e2e-applypause.yaml
kubectl apply -f …/rendered/pipelines
kubectl apply --prune -l htrflow.riksarkivet.se/managed-by=converter \
  -f …/rendered/pipelines -f …/rendered/campaigns
…
configmap/campaign-e2e-applypause pruned
job.batch/e2e-applypause pruned
```

Note the pruning apply is handed **both** directories. `--prune -l` deletes
every object carrying the label that is not in *this* apply, and the pipeline
ConfigMaps and warm-up Jobs carry it too — pruning against `campaigns/` alone
would have deleted the two `htr-pipeline-*` ConfigMaps and both warm-up Jobs
that the previous command had just applied. After the prune both pipeline
ConfigMaps were still there.

## Cluster state left behind

`e2e-applyrun` (Complete 2/2) joins the completed campaigns from the earlier
rounds; `e2e-applypause`, `e2e-qlabel` and `e2e-qneighbour` are gone. No
campaign pod is running, and the ClusterQueue is idle
(`admitted=0 pending=0 gpu_used=0`). Campaigns repo `~/htr-test` branch
`b63-indexed` (still never pushed): `8b338b6`.

!!! note "GPU flakiness during this run"

    Several indexes failed a first attempt with `torch.AcceleratorError: CUDA
    error: out of memory` and succeeded on the retry — on this node, with no
    other GPU process visible to `nvidia-smi` and 70 GB of the unified 119 GB
    free. It is an environment condition, not a campaign or converter bug
    (`backoffLimitPerIndex: 3` absorbed it), but it is why a two-volume
    campaign took 81 s here and 20 s per volume in fix round 2.

## Task 15 — the PoC bucket moved to the namespaced layout

The chart/converter flag that kept the flat `<pipeline>/<volume>/` layout
existed only because this bucket predates `<namespace>/<pipeline>/<volume>/`.
It was retired by moving the data once
(2026-09-02). Credentials came from the `htr-batch-s3` Secret into the
environment; every command below is `aws --endpoint-url
http://localhost:30900 …`.

### Before

Twenty top-level prefixes, 1 864 objects: eighteen pipeline ids written by
the pre-B63 reconciler and the B63 e2e rounds, plus `sources/` (the
synthetic IIIF manifests for `IMAGES` volumes) and `status/`. No `htr-batch/`
prefix existed — the PoC ran on the flat layout throughout.

```console
$ aws s3 ls s3://htr-results/
   PRE e2e-slow-v1/  e2e-v1/  eng-modern-v2/  eng-modern-v3/  local-v2-gpu/
   PRE local-v3/  local-v4/  local-v5/  local-v6/  local-v7/
   PRE medieval-v1/  medieval-v2/  nor-singlepage-v1/  nor-singlepage-v2/
   PRE sources/  status/  swe-singlepage-v1/  swe-singlepage-v2/
   PRE swe-spreads-v1/  swe-spreads-v2/
$ aws s3 ls --recursive s3://htr-results/ | wc -l
1864
$ aws s3 ls --recursive s3://htr-results/htr-batch/ | wc -l
0
```

| prefix | objects | prefix | objects |
|---|---:|---|---:|
| `e2e-slow-v1/` | 43 | `medieval-v1/` | 5 |
| `e2e-v1/` | 539 | `medieval-v2/` | 5 |
| `eng-modern-v2/` | 5 | `nor-singlepage-v1/` | 5 |
| `eng-modern-v3/` | 5 | `nor-singlepage-v2/` | 5 |
| `local-v2-gpu/` | 7 | `sources/` | 107 |
| `local-v3/` | 7 | `swe-singlepage-v1/` | 11 |
| `local-v4/` | 11 | `swe-singlepage-v2/` | 11 |
| `local-v5/` | 7 | `swe-spreads-v1/` | 9 |
| `local-v6/` | 7 | `swe-spreads-v2/` | 963 |
| `local-v7/` | 9 | **`status/` (stays)** | **103** |

`sources/` moves with the rest: the wrapper prefixes `S3_PREFIX` in *front*
of the `sources/` key (`ResultStore.put_json_at`), so the namespaced form is
`htr-batch/sources/<pipeline>/<volume>/manifest.json`, not
`sources/<namespace>/…`. `status/` is namespace-free by design
(`ResultStore.run_log_key`) and was not touched — it still holds
`status/logs/`, plus `status/attempts.json` and `status/failures/` left by
the retired reconciler.

### Dry-run, then the move

For each prefix `<p>` other than `status/`:

```console
$ aws s3 mv --recursive --dryrun s3://htr-results/<p>/ s3://htr-results/htr-batch/<p>/
$ aws s3 mv --recursive         s3://htr-results/<p>/ s3://htr-results/htr-batch/<p>/
```

The dry-run listed exactly the per-prefix counts above (1 761 moves in
total, = 1 864 − 103). The real run moved the same 1 761; after each prefix
the source listed 0 objects and the destination listed the count it started
with (nothing pre-existed under `htr-batch/`). No `rm`, no `delete` — `mv`
only.

### After

```console
$ aws s3 ls s3://htr-results/
                           PRE htr-batch/
                           PRE status/
$ aws s3 ls --recursive s3://htr-results/ | wc -l
1864
$ aws s3 ls --recursive s3://htr-results/htr-batch/ | wc -l
1761
$ aws s3 ls --recursive s3://htr-results/status/ | wc -l
103
```

Zero loss, verified key by key: the sorted key list before, with every
non-`status/` key prefixed `htr-batch/`, is byte-identical to the sorted key
list after (`diff` empty).

Old absolute URLs recorded *inside* results (`viewer_url`, `source_manifest`
in each `manifest.json`) still name the pre-move paths and now 404. Nothing
resolves them at runtime — the read API rebuilds every link from
`resultsBase`, and a re-run rewrites the manifest — so they were left alone
rather than rewritten.
