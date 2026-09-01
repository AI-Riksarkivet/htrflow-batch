# Campaigns as Indexed Jobs — design (B63, #2978)

Status: reviewed with Morgan 2026-09-01 · supersedes
`2026-08-31-transcriptionjob-controller-design.md` (CRD + Go controller,
abandoned after Task 7; branch `b63-controller` kept as reference).

## 1. Goal

A campaign is **one Kubernetes Indexed Job**. Kubernetes and Kueue own scheduling,
retries, progress and pause; we own only the wrapper (unchanged core), a pure
converter from campaign YAML to manifests, a thin read API for the status
page, and the chart. No CRD, no controller, no state files in the bucket.

**Size is a requirement** (checked by `scripts/loc-budget.sh`, non-test lines):
wrapper ≤ 1 500 · converter ≤ 400 · read API ≤ 400 · frontend ≤ 2 500 · chart
≤ 700 template lines · **Python only** in the batch system (Svelte in the
frontend). Dead code is deleted in the PR that makes it dead.

**Non-goals:** external API/auth (T01/T03), uploads (T04), per-org quotas beyond
one Kueue queue per namespace (T05), retention (T10). The design leaves room;
it does not build them.

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **One `batch/v1` Indexed Job per campaign**: `completionMode: Indexed`, `completions` = number of volumes, `parallelism` = window (default 20), `backoffLimitPerIndex: 3`, `maxFailedIndexes` = completions, `podFailurePolicy` (exit 13 → `FailIndex`; `DisruptionTarget` → `Ignore`), `ttlSecondsAfterFinished: 86400`, `restartPolicy: Never`, Kueue labels `queue-name` (+ `priority-class` when set) and annotation `kueue.x-k8s.io/job-min-parallelism: "1"` | Per-volume retry, progress (`completedIndexes`/`failedIndexes`), suspend/resume and fair queueing come from Kubernetes ≥ 1.29 and Kueue; nothing to reconcile. Verified against the official docs and KEP-3850. |
| D2 | **Volume list = one ConfigMap per campaign**, `volumes.txt`, one line per index: `<id>\t<manifest-url>` or `<id>\timages:<url1>,<url2>,…`. ≤ 10 000 volumes per Job; larger campaigns are split `-part1`, `-part2` by the converter | ConfigMaps ≤ 1 MiB; Job status fields stay small; a campaign is append-only (Job `completions` is immutable) — editing volumes means a new campaign, enforced at validate time. |
| D3 | **Converter is a pure function** `campaigns/ + pipelines/ → manifests` (Python package `packages/converter`, CLI `htrflow-campaigns validate|render`). It never talks to the cluster or S3 | Reviewable output (`git diff` of rendered YAML); reusable by the ATRaaS API later (T03) as a library; no Argo plugin sidecar. |
| D4 | **Rendered manifests are committed** to the campaigns repo (`rendered/`) by that repo's CI on merge; Argo CD's Application points at `rendered/`. On the PoC `make campaigns-apply` renders and applies | Argo stays plain (no config-management plugin); what runs is literally in Git. |
| D5 | **Pipelines** stay `pipelines/<id>.yaml`; the converter renders `ConfigMap htr-pipeline-<id>` (`pipeline.yaml`) and the warm-up Job `htr-warmup-<id>` (CPU, outside Kueue). Batch pods wait for the warm-up marker file on the cache PVC in an init check (today's gate) | The chart no longer renders pipelines; pipelines live with campaigns. |
| D6 | **Wrapper gains two small features, nothing else**: `MAX_SECONDS` (per-volume time limit; exit 1 → retried by the index backoff) and `IMAGES` (comma-separated image URLs as an alternative to `IIIF_MANIFEST_URL`; the wrapper builds the synthetic manifest and publishes it to `sources/<pipeline>/<volume>/manifest.json` itself) | `activeDeadlineSeconds` is whole-Job; and D3 requires the converter to stay pure, so the S3 write moves to the pod that already has S3 credentials. |
| D7 | **Wrapper loses** thumbnails and `metrics-failed-latest.json` | Not core; the reconciler was their only consumer. |
| D8 | **Read API** (`packages/api`, Python, FastAPI + kubernetes client, read-only RBAC on Jobs/Pods/ConfigMaps in tenant namespaces): `GET /api/v1/jobs` → per campaign `{namespace, name, pipeline, phase, counts{total,active,done,failed}, suspended, createdAt, resultsBase}`; `GET /api/v1/jobs/{ns}/{name}?offset&limit` → rows per index from the ConfigMap lines × `completedIndexes`/`failedIndexes`/active pods, with S3 links by convention and, for failed indexes whose pod still exists, the termination message as `reason`. Served on `:8081`, proxied by the viewer nginx at `/api/` | Replaces `status.json`; the seed of T03. Phase: `Queued` (suspended, 0 done) · `Paused` (suspended, >0 done) · `Running` · `Succeeded` · `Failed` (Job condition). |
| D9 | **S3 layout** `<namespace>/<pipeline>/<volume>/…` via `S3_PREFIX=<namespace>/`; chart value `legacyLayout: true` keeps today's `<pipeline>/<volume>/` for Riksarkivet's existing data | Tenant dimension (T13) decided now; the wrapper already honours `S3_PREFIX`. |
| D10 | **Pause = `suspend: true` in the rendered Job (a Git change); cancel = delete the campaign file (Argo prunes the Job and ConfigMap). S3 is never deleted by anything here** | Kubernetes semantics; retention is T10. |
| D11 | **Run log shipping stays as is for now** (`logship.py`, 15 s) — open decision: cut to "ship on exit only" or drop in favour of central logging once the dev cluster's Fluent Bit path is confirmed to reach data scientists | Keeps the status page's live log until we know what replaces it. |
| D12 | **Delete**: `packages/reconciler` (its `parse.py`/`jobspec.py` logic moves into the converter), `reconciler.yaml`, `pipelines.yaml`, `job-example.yaml`, `devstack-*` templates (→ `charts/htrflow-devstack`), `exampleJob`/`reconciler`/`pipelines`/`devStack` values, the four status files' readers in the frontend, the `b63-controller` Go module (never merged) | The point of the exercise. |

## 3. Objects the converter renders

Per pipeline `pipelines/<id>.yaml` (`image` digest-pinned, `steps`):
- `ConfigMap htr-pipeline-<id>` — `pipeline.yaml: {steps: …}`; sha256 of that text as annotation `htrflow.riksarkivet.se/pipeline-sha256` (the drift ground truth the wrapper records).
- `Job htr-warmup-<id>` — the `build_warmup_job` output, unchanged.

Per campaign `campaigns/<name>.yaml` (`pipeline`, `volumes[]` as today):
- `ConfigMap campaign-<name>` — `volumes.txt`.
- `Job <name>` — the `build_job` pod template (security contexts, mounts, env, resources, runtimeClass/nodeSelector/tolerations from converter config), with `command: ["/bin/sh","-c"]` and args that read line `$JOB_COMPLETION_INDEX+1` of `/campaign/volumes.txt`, export `VOLUME_REF` and either `IIIF_MANIFEST_URL` or `IMAGES`, then `exec python -m htrflow_batch`; an init container that waits for `/data/warmup/<pipeline>.done`.
Labels on everything: `htrflow.riksarkivet.se/{campaign,pipeline,managed-by=converter}`, `app: htrflow-batch` (NetworkPolicies select on it).

Converter config (`converter.yaml` in the campaigns repo, or flags): namespace, queue, window, S3 secret name, data PVC, runtime class, node selector, tolerations, `publicResultsBase`, `legacyLayout`, `sourceTemplate`, `maxSeconds`, byte caps.

Validation (`validate`, run in CI on every PR and by `render`): pipeline exists and is digest-pinned; ids match `[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?`, unique; `manifest`/`images` http(s) only; ≤ 10 000 volumes per part; **a campaign whose rendered Job already exists in `rendered/` with a different volume list is rejected** ("campaigns are append-only; create a new campaign").

## 4. Data flow and failure handling

PR → CI validate → merge → CI `render` commits `rendered/` → Argo applies → Kueue
suspends, then admits (possibly with reduced parallelism) → pod *i* runs one
volume → per-page ALTO/PAGE streamed → `manifest.json` last → index completed.
Exit 13 → `FailIndex`; exit 1/`MAX_SECONDS` → retried ≤ 3; SIGTERM/143 →
`Ignore`d, index restarts, resume skips done pages. `kubectl describe job`
and the read API show progress; Prometheus gets Job metrics via kube-state-metrics
(`kube_job_status_succeeded/failed`) — no exporter of our own.

## 5. Chart (0.3.0)

Keep: `kueue.yaml`, `network.yaml` (+ API pod egress to the API server only;
ingress from viewer), `viewer.yaml` (+ `/api/` proxy), `modelcache.yaml`,
`validate.yaml`, `kyverno.yaml`. Add: `api.yaml` (Deployment, SA, Role/RoleBinding
read-only on jobs/pods/configmaps, Service `:8081`). Remove: D12 list. Values:
`api.image`, `api.resources`, `legacyLayout`; `queue.*` unchanged. New chart
`charts/htrflow-devstack` holds the PoC-only objects.

## 6. Testing

- Converter: golden tests (fixture campaigns → expected YAML), validation
  error cases, split at 10 000, `kubeconform` on rendered output in CI.
- Wrapper: unit tests for `MAX_SECONDS` (exit 1 with termination message) and
  `IMAGES` (synthetic manifest published, pages processed); thumbnail/failure
  metrics tests deleted.
- Read API: tests with a fake Kubernetes client (Job with `completedIndexes:
  "0-2,5"`, `failedIndexes: "3"`, one active pod with a termination message).
- Frontend: fixtures for the two endpoints; derivation layer deleted.
- Chart: `helm template` + kubeconform; render assertions (no CronJob, API
  Deployment present, NetworkPolicy egress for `network.sources`).
- E2E on the PoC (k3s ≥ 1.29 — check `kubectl version` first): 50-volume
  campaign → all indexes complete, viewer opens, no `status/*.json` written;
  `suspend: true` mid-run keeps done indexes; partial admission with a
  1-GPU quota; delete campaign file → Job pruned, S3 untouched.

## 7. Rollout

1. Chart 0.3.0 on the PoC at a quiet point; CronJob reconciler deleted; campaigns
   repo gets `converter.yaml`, CI, `rendered/`.
2. Existing S3 results need no migration (`legacyLayout: true` on the PoC and
   for Riksarkivet's namespace in dev).
3. I15 deploys chart 0.3.0 in dev; the Argo Application for campaigns points at
   `rendered/` in the Azure Repos campaigns repo.
4. Docs: how-it-works and reference rewritten around Jobs; campaigns-repo README
   states in bold: **pause is a Git change; deleting a campaign file cancels it
   and prunes its Job; results stay in S3.**
