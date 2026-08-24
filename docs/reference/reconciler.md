# Reconciler

The GitOps campaign reconciler (`htrflow-reconciler`, module
`htrflow_reconciler`). One CronJob tick = clone the campaigns repo, three-way
join git ↔ S3 ↔ cluster Jobs, submit what's missing, publish `status.json`.
The narrative is in [How it Works → Campaigns](../how-it-works/campaigns.md);
this page is the surface reference.

## Modules

Source root: [`packages/reconciler/src/htrflow_reconciler/`](https://github.com/carpelan/test/tree/main/packages/reconciler/src/htrflow_reconciler)

| Module | Description |
|--------|-------------|
| `models.py` | Domain types: `Volume`, `Campaign`, `PipelineSpec` (frozen Pydantic models) |
| `parse.py` | Campaign/pipeline YAML → domain types; id validation ([reference](campaign-yaml.md)) |
| `status.py` | `job_name()` and `derive()` — the pure three-way join per volume |
| `plan.py` | `plan_submissions()` — round-robin across campaigns into the free window |
| `guards.py` | `check_drift()` — ConfigMap + published-manifest immutability checks |
| `jobspec.py` | `build_job()` — the Job dict (digest-pinned image, `IMAGE_DIGEST` provenance env, `S3_PREFIX=""`) and `ReconcilerConfig` |
| `synthetic.py` | Synthetic IIIF manifests for `images:` volumes; `classify_manifest` (P2/P3/unsupported) |
| `s3.py` | Key helpers + `Bucket` (thin boto3 shell; HEAD-based `done_volumes`) |
| `k8s.py` | `Cluster` — Jobs list/create/delete/logs, ConfigMap read/apply |
| `gitrepo.py` | `checkout()` — clone/pull with `GIT_TERMINAL_PROMPT=0` and credential redaction |
| `main.py` | `tick()` — one reconcile pass, adapters injected, pure orchestration |
| `__main__.py` | CronJob entrypoint: pydantic-settings config from env, real adapters |

## Settings (environment)

`__main__.Settings` is a `pydantic_settings.BaseSettings` — every field is an
env var (upper-cased). The chart supplies all of them; the defaults reproduce
the PoC.

| Env var | Default | Description |
|---------|---------|-------------|
| `CAMPAIGNS_REPO_URL` | *(required)* | Git URL of the campaigns repo (anonymous HTTPS clone) |
| `PUBLIC_RESULTS_BASE` | *(required)* | Browser-reachable base URL of the results bucket |
| `S3_ENDPOINT` | `""` | S3 endpoint; empty = boto3 provider default chain |
| `S3_BUCKET` | `htr-results` | Results bucket |
| `CAMPAIGNS_DIR` | `$TMPDIR/campaigns` | Where the repo is checked out |
| `RECONCILER_WINDOW` | `20` | Max Jobs that are neither `Complete` nor `Failed` at once |
| `RECONCILER_ATTEMPT_CAP` | `3` | Retries per (pipeline, volume) before `needs-attention` |
| `RECONCILER_NAMESPACE` | `htr-batch` | Namespace for Jobs/ConfigMaps (chart: downward API) |
| `RECONCILER_QUEUE` | `htr-batch` | Kueue LocalQueue label on submitted Jobs |
| `RECONCILER_S3_SECRET` | `htr-batch-s3` | Secret injected into Jobs via `envFrom` |
| `RECONCILER_DATA_PVC` | `htr-test-data` | PVC mounted at `/data` (HF model cache) |

AWS credentials come from the same secret via `envFrom` on the CronJob
(`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_ENDPOINT`, `S3_BUCKET`).

## Volume states

`status.derive()` — done-set first, then the Job snapshot; validation verdicts
can override before submission:

| State | Meaning |
|-------|---------|
| `done` | `manifest.json` exists under `<pipeline>/<volume>/` — S3 is the authority |
| `running` | Job exists and is active |
| `queued` | Job exists, not active, not terminal (Kueue-suspended, or manifest not yet visible) |
| `retry` | Job hit terminal `Failed` and budget remains — logs captured, Job deleted, resubmitted |
| `needs-attention` | Terminal failure with exit code 13 (permanent) or attempt cap reached |
| `pending` | No Job, no result yet — a submission candidate |
| `unreachable` | Source manifest fetch failed — **not cached**, re-probed next tick |
| `unsupported` | Source manifest is not a readable P2/P3 Manifest (e.g. a Collection) — cached forever |

## Job naming and ownership

`job_name(pipeline_id, volume_id)` →
`htr-<slug>-<8hex>`: the slug is the sanitized pair (≤54 chars), the suffix an
8-char sha256 over `pipeline\x00volume`, so distinct pairs can never collide
and the name is always a valid DNS-1123 label.

Submitted Jobs carry `app=htrflow-batch` **and**
`batch.htrflow/managed-by=reconciler`; only the latter count against the
window, so hand-run experiments don't eat campaign throughput. Jobs are
created suspended (`suspend: true`) for Kueue, `backoffLimit: 0`,
`activeDeadlineSeconds: 21600`, TTL 24 h after finish.

## The tick

`main.tick()` in order: load repo → drift-check every pipeline (before
`ensure_configmap`) → per campaign, derive each volume's state, validate
external manifests (thumbnail + format), capture logs / delete / bump attempts
for `retry` volumes → round-robin submit into the free window → write
`validation.json`, `attempts.json`, `status.json`. Runs every
`TICK_SECONDS = 300` (chart schedule `*/5 * * * *`).
