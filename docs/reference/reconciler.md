# Reconciler

The GitOps campaign reconciler (`htrflow-reconciler`, module
`htrflow_reconciler`). One CronJob tick = take the Lease, clone the campaigns
repo, three-way join git ↔ S3 ↔ cluster Jobs, submit what's missing, publish
`status.json`. The narrative is in
[How it Works → Campaigns](../how-it-works/campaigns.md) and
[Failure Handling](../how-it-works/failure-handling.md); this page is the
surface reference.

## Modules

Source root: [`packages/reconciler/src/htrflow_reconciler/`](https://github.com/carpelan/test/tree/main/packages/reconciler/src/htrflow_reconciler)

| Module | Description |
|--------|-------------|
| `models.py` | Domain types: `Volume`, `Campaign`, `PipelineSpec` (frozen Pydantic models) |
| `parse.py` | Campaign/pipeline YAML → domain types; id validation, image allow-list, `revision:` check, http(s)-only sources, `canonical_sha256` ([reference](campaign-yaml.md)) |
| `attempts.py` | `Attempt {n, terminal, pages_at_submit}` and the v1→v2 migration of `status/attempts.json` |
| `status.py` | `job_name()`, `JobState`, `is_permanent()` and `derive()` — the pure three-way join per volume |
| `plan.py` | `plan_submissions()` — round-robin across campaigns into the free window |
| `guards.py` | `check_drift()` — ConfigMap + published-manifest immutability checks |
| `jobspec.py` | `build_job()` / `build_warmup_job()` — the Job dicts (digest-pinned image, `IMAGE_DIGEST` provenance env, `S3_PREFIX=""`, `podFailurePolicy`, page-derived deadline) and `ReconcilerConfig` |
| `synthetic.py` | Synthetic P3 manifests for `images:` volumes; `classify_manifest` (P2/P3/unsupported) |
| `s3.py` | Key helpers + `Bucket` (thin boto3 shell; pooled HEAD-based `done_volumes`, `count_pages`, a `calls` counter) |
| `k8s.py` | `Cluster` — Lease acquire/release, Jobs list/create/delete/logs, ConfigMap read/apply |
| `gitrepo.py` | `checkout()` — shallow clone/fetch with **dulwich** (no `git` binary in the image), `GIT_TOKEN` auth, userinfo redaction, socket timeout |
| `main.py` | `tick()` and `_Pass` — one reconcile pass, adapters injected, every S3/kube effect contained per volume |
| `warmup.py` | CLI that renders one warm-up Job as JSON (`make warmup`), for chart-declared pipelines |
| `__main__.py` | CronJob entrypoint: pydantic-settings config from env, real adapters, `fetch_json` with the S5 guards |

## Settings (environment)

`__main__.Settings` is a `pydantic_settings.BaseSettings` — every field is an
env var (upper-cased). The chart renders all of them
([Chart Values → Reconciler](chart.md#reconciler-reconciler)); the defaults
reproduce the PoC.

| Env var | Default | Description |
|---------|---------|-------------|
| `CAMPAIGNS_REPO_URL` | *(required)* | Git URL of the campaigns repo: `https://` (anonymous, or token) or `git://`; local paths and `file://` for tests |
| `CAMPAIGNS_REPO_WEB_URL` | `""` | Browsable URL published as `campaigns_repo_url` in `status.json`; falls back to `CAMPAIGNS_REPO_URL` |
| `GIT_TOKEN` | *(unset)* | Read-only token for an `https://` repo, sent as user `x-access-token`; a `user:token@` in the URL wins over it. Mount from a Secret |
| `PUBLIC_RESULTS_BASE` | *(required)* | Browser-reachable base URL of the results bucket |
| `S3_ENDPOINT` | `""` | S3 endpoint; empty = boto3 provider default chain (real AWS). When set, `<endpoint>/<bucket>` is the **internal results base** Jobs use to fetch synthetic manifests |
| `S3_BUCKET` | `htr-results` | Results bucket |
| `AWS_SHARED_CREDENTIALS_FILE` | *(boto3 default)* | The chart sets `/secrets/s3/credentials` — the mounted `credentials` key of the S3 Secret. Credentials are never env |
| `CAMPAIGNS_DIR` | `$TMPDIR/campaigns` | Where the repo is checked out (the chart gives `/tmp` an emptyDir) |
| `RECONCILER_WINDOW` | `20` | Max Jobs in flight — pending, running or Terminating — at once |
| `RECONCILER_ATTEMPT_CAP` | `3` | Attempts per (pipeline, volume), and per pipeline warm-up, before `needs-attention` |
| `RECONCILER_NAMESPACE` | `htr-batch` | Namespace for Jobs/ConfigMaps/Lease (chart: downward API) |
| `RECONCILER_QUEUE` | `htr-batch` | Kueue LocalQueue label on submitted Jobs |
| `RECONCILER_S3_SECRET` | `htr-batch-s3` | Secret each Job mounts at `/secrets/s3` (`credentials` file, `0440`) and reads `S3_ENDPOINT`/`S3_BUCKET` from |
| `RECONCILER_DATA_PVC` | `htr-test-data` | PVC mounted at `/data` (HF model cache; `HF_HOME=/data/hf`) |
| `RECONCILER_TICK_SECONDS` | `300` | Emitted as `tick_seconds` (STALE threshold = 3×); also the unit of the `unreachable` back-off. Must match the CronJob schedule |
| `RECONCILER_TICK_DEADLINE_SECONDS` | `600` | One tick's wall-clock budget: the Lease duration and the clamp on the git timeout; equals the CronJob's `activeDeadlineSeconds` |
| `RECONCILER_GIT_TIMEOUT` | `300` | Socket timeout for the dulwich clone/fetch, clamped to the tick deadline |
| `RECONCILER_LEASE_NAME` | `htr-reconciler` | `coordination.k8s.io` Lease taken per tick |
| `RECONCILER_ALLOWED_IMAGE_REPOS` | `""` | Comma-separated repository prefixes a pipeline image may use (`ghcr.io/riksarkivet/`); empty = any digest-pinned image, with a warning in `status.json` |
| `RECONCILER_REQUIRE_MODEL_REVISION` | `false` | Every `model_settings.model` needs a 40-hex `revision:` |
| `RECONCILER_JOB_MIN_DEADLINE_SECONDS` | `21600` | Job `activeDeadlineSeconds = max(min, pages × per_page)` |
| `RECONCILER_JOB_SECONDS_PER_PAGE` | `30` | see above |
| `RECONCILER_JOB_RUNTIME_CLASS` | `nvidia` | `""` omits `runtimeClassName` |
| `RECONCILER_JOB_NODE_SELECTOR` | `{}` | JSON object |
| `RECONCILER_JOB_TOLERATIONS` | `[]` | JSON array of toleration objects |
| `RECONCILER_JOB_MANIFEST_MAX_BYTES` | `16777216` | Passed to Jobs as `MANIFEST_MAX_BYTES` |
| `RECONCILER_JOB_FETCH_MAX_BYTES` | `67108864` | Passed to Jobs as `FETCH_MAX_BYTES` |
| `RECONCILER_MAX_VALIDATIONS_PER_TICK` | `50` | Source manifests fetched per tick |
| `RECONCILER_FETCH_MAX_BYTES` | `16777216` | Byte cap on the reconciler's own manifest fetch |

Not an env var: `ReconcilerConfig.unreachable_ticks` (3) — how many ticks a
transiently unreachable manifest is left alone before it is re-probed.

## Volume states

`status.derive()` — done-set first, then the persisted terminal verdict, then
the Job snapshot; pre-validation verdicts can override before submission:

| State | Meaning |
|-------|---------|
| `done` | `manifest.json` exists under `<pipeline>/<volume>/` — S3 is the authority, never re-checked |
| `needs-attention` | **Sticky**: `attempts.json` carries `terminal: "exit-13"` (wrapper said permanent) or `"capped"` (attempt cap reached). Held whether or not the Job still exists; also derived on first sight of a Failed Job with exit 13 / reason `PodFailurePolicy`, or with `n ≥ attemptCap`, and persisted at once. Clearing it is an operator action: delete the `<pipeline>/<volume>` key in `status/attempts.json`, or re-run under a new pipeline id |
| `deleting` | The Job has a `deletionTimestamp` (a retry's Foreground delete in progress; its pod may still hold the GPU, so it occupies a window slot). Back to `pending` the tick after |
| `retry` | Job hit terminal `Failed`, not permanent, budget remains — evidence captured to `status/failures/…`, attempt bumped and persisted, Job deleted; resubmitted **next** tick |
| `running` | Job exists and is active |
| `queued` | Job exists, not active, not terminal (Kueue-suspended, or Complete but `manifest.json` not yet visible) |
| `pending` | No Job, no result — a submission candidate (once its manifest has been validated) |
| `unreachable` | Source manifest fetch failed. 4xx (400/401/403/404/410) is **permanent** and cached forever; 5xx/429/network is transient and re-probed after `unreachable_ticks × tick_seconds` (15 min) |
| `unsupported` | Non-http(s) URL, non-JSON, over the byte cap, a Collection, or a document that is neither a readable P2 nor P3 Manifest — cached forever |

The frontend renders anything else as `unknown`.

## Job naming and ownership

`job_name(pipeline_id, volume_id)` →
`htr-<slug>-<8hex>`: the slug is the sanitized pair (≤54 chars), the suffix an
8-char sha256 over `pipeline\x00volume`, so distinct pairs can never collide
and the name is always a valid DNS-1123 label. Warm-ups are
`htr-warmup-<pipeline>`.

Submitted Jobs carry `app=htrflow-batch` **and**
`batch.htrflow/managed-by=reconciler` (only those are listed and count
against the window, so hand-run experiments don't eat campaign throughput),
plus `batch.htrflow/volume`, `batch.htrflow/pipeline` and
`batch.htrflow/campaign` (the campaign file stem, label-sanitised — what the
fairness order counts). The Job contract itself — `suspend: true`,
`backoffLimit: 0`, `podFailurePolicy`, the page-derived deadline, TTL 24 h —
is in [Failure Handling](../how-it-works/failure-handling.md#the-job-contract).

## The tick

`main.tick()`:

1. **Lease.** Acquire `RECONCILER_LEASE_NAME` for `tick_deadline_seconds`; if
   another tick holds it (a manual `kubectl create job --from=cronjob/…`
   while one runs), log `tick skipped: lease … is held` and exit 0 without
   touching S3 or the cluster. A tick killed by the deadline leaves its Lease
   to expire.
2. **Load.** Read campaigns and pipelines from the checkout (a malformed or
   unreadable file is contained to that campaign / pipeline); snapshot the
   managed Jobs; read `status/attempts.json` (v1 ints migrated),
   `status/validation.json` and `status/volumes.json` — a corrupt owned file
   is treated as absent with a warning, never a poison pill.
3. **Pipelines.** Drift-check every pipeline *before* `ensure_configmap`
   ([guards](campaign-yaml.md#immutability)); a failing one is blocked for the tick.
4. **Warm-up gate.** For every pipeline that still has volumes to run:
   blocked until its `htr-warmup-<id>` Job has `Complete`. Missing → create;
   Failed → log to `status/warmup/<id>.log`, charge `warmup/<id>` in
   `attempts.json`, and either delete/recreate next tick or park it
   (`terminal` = `exit-13` on a permanent config error, `capped` at the
   attempt cap) with a `needs attention` warning.
5. **Bounded pre-validation.** Fetch up to `max_validations_per_tick`
   not-yet-validated `manifest:` URLs, 8 at a time with a 10 s timeout,
   `max_redirects=3` and the byte cap; persist the verdicts (`format`,
   `thumbnail`, `page_count`, `permanent`, `unreachable_until`) at once, so a
   deadline-killed tick keeps what it paid for. Volumes past the bound wait.
6. **Per campaign, per volume** (each effect wrapped so one bad S3/kube
   response marks that row `error` and the tick goes on): write the synthetic
   manifest for `images:` volumes when its key changed; derive the state;
   apply the validation verdict; on `retry` bump-and-persist the attempt
   (unless the run made progress — see Failure Handling), preserve the
   failure log, delete the Job; on first-sight `needs-attention` persist the
   terminal verdict and preserve the log; fill `pages_done`/`pages_total`,
   links and the thumbnail. Done volumes are probed once per `manifest.json`
   mtime and then served from `status/volumes.json`.
7. **Submit.** Campaigns ordered by Jobs in flight (fewest first, then name),
   round-robin into `window − in_flight`; each Job gets
   `activeDeadlineSeconds` from its page count, and `pages_at_submit` is
   recorded in `attempts.json`.
8. **Write** `attempts.json`, `volumes.json`, then `status.json` with
   `tick_summary {seconds, s3_calls, validations, submitted, retried}`, and
   log one line: `tick: seconds=… s3_calls=… validations=… submitted=…
   retried=… warnings=…`.
9. Release the Lease.

Runs every `RECONCILER_TICK_SECONDS` (chart schedule `*/5 * * * *`).
