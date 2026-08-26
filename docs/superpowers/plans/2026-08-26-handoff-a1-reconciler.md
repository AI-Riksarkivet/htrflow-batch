# Handoff — A1 reconciler (audit remediation 2026-08-26)

What the reconciler package now expects from the other work packages. Every
item here is something A1 could not do inside `packages/reconciler/**` and
`.docker/htrflow-reconciler.dockerfile`.

## A3 chart / ops

### RBAC (blocking — the tick refuses to run without it)

The tick takes a `coordination.k8s.io` Lease named `RECONCILER_LEASE_NAME`
(default `htr-reconciler`) in its own namespace. Add to the reconciler Role:

```yaml
- apiGroups: ["coordination.k8s.io"]
  resources: ["leases"]
  verbs: ["get", "create", "update"]
```

Without it `acquire_lease` raises 403 and the tick exits non-zero on every
run. A held Lease (a manual `kubectl create job --from=cronjob/...` while a
tick runs) makes the second tick log `tick skipped: lease ... is held` and
exit 0 without touching S3 or the cluster. A tick killed by the deadline
leaves its Lease behind; it expires after `RECONCILER_TICK_DEADLINE_SECONDS`.

### Env (reconciler CronJob container)

All names/defaults from the plan's contract table are read by
`htrflow_reconciler.__main__.Settings` (`build_config`). Render them; the
ones not in the plan are marked *(new)*:

| Env | Default | Notes |
|---|---|---|
| `RECONCILER_TICK_SECONDS` | `300` | STALE threshold; must equal the schedule interval |
| `RECONCILER_TICK_DEADLINE_SECONDS` | `600` | = CronJob `activeDeadlineSeconds`; also the Lease duration and the git-timeout clamp |
| `RECONCILER_GIT_TIMEOUT` *(new)* | `300` | socket timeout for the dulwich clone/fetch; clamped to the tick deadline |
| `RECONCILER_LEASE_NAME` | `htr-reconciler` | |
| `RECONCILER_DATA_PVC` | `htr-test-data` | unchanged |
| `RECONCILER_ALLOWED_IMAGE_REPOS` | `""` | comma-separated repo prefixes (`ghcr.io/riksarkivet/`); empty = any digest-pinned image **and a warning in status.json** — set it in production values |
| `RECONCILER_REQUIRE_MODEL_REVISION` | `false` | `true`/`false` |
| `RECONCILER_JOB_MIN_DEADLINE_SECONDS` | `21600` | |
| `RECONCILER_JOB_SECONDS_PER_PAGE` | `30` | |
| `RECONCILER_JOB_RUNTIME_CLASS` | `nvidia` | empty string omits `runtimeClassName` |
| `RECONCILER_JOB_NODE_SELECTOR` | `{}` | JSON object (pydantic-settings parses it) |
| `RECONCILER_JOB_TOLERATIONS` | `[]` | JSON array of toleration objects |
| `RECONCILER_MAX_VALIDATIONS_PER_TICK` | `50` | |
| `RECONCILER_FETCH_MAX_BYTES` | `16777216` | reconciler's own manifest fetch cap |
| `RECONCILER_JOB_MANIFEST_MAX_BYTES` *(new, A2 contract)* | `16777216` | passed to Jobs as `MANIFEST_MAX_BYTES` |
| `RECONCILER_JOB_FETCH_MAX_BYTES` *(new, A2 contract)* | `67108864` | passed to Jobs as `FETCH_MAX_BYTES` |
| `GIT_TOKEN` *(new, optional)* | — | read-only token for an `https://` campaigns URL (sent as `x-access-token:<token>`); alternatively embed it as URL userinfo. Mount from a Secret, never a value |

Existing env (`CAMPAIGNS_REPO_URL`, `CAMPAIGNS_REPO_WEB_URL`,
`PUBLIC_RESULTS_BASE`, `S3_*`, `RECONCILER_NAMESPACE/QUEUE/S3_SECRET/WINDOW/
ATTEMPT_CAP`) is unchanged.

### CronJob spec

`activeDeadlineSeconds: 240` in `reconciler.yaml` must become
`reconciler.tickDeadlineSeconds` (default 600) and `startingDeadlineSeconds:
120` added, per the plan. The tick logs one `tick: seconds=... s3_calls=...
validations=... submitted=... retried=... warnings=...` line at INFO; the
entrypoint now calls `logging.basicConfig(INFO)`.

### `job-example.yaml` mirror

`jobspec.build_job` now renders:

- `podFailurePolicy: {rules: [{action: Ignore, onPodConditions: [{type: DisruptionTarget}]}, {action: FailJob, onExitCodes: {containerName: wrapper, operator: In, values: [13]}}]}` (warm-up Jobs the same with `containerName: warmup`)
- `activeDeadlineSeconds = max(minDeadline, pages × secondsPerPage)`
- label `batch.htrflow/campaign: <campaign file stem, label-sanitised>`
- env `MANIFEST_MAX_BYTES`, `FETCH_MAX_BYTES`
- `nodeSelector` / `tolerations` when configured

`backoffLimit: 0`, TTL 86400 and the resource requests are unchanged.

### Image

`.docker/htrflow-reconciler.dockerfile` no longer installs `git` (dulwich does
the clone) and drops `GIT_SSL_CAINFO`; `SSL_CERT_FILE` stays. No Makefile or
dagger change is needed for the reconciler image, but anything that assumed
`git` inside the reconciler container (none found in scope) would break.

### S3 objects the reconciler now owns

`status/volumes.json` (probe cache, safe to delete — it is rebuilt) joins
`status/attempts.json`, `status/validation.json`, `status/status.json`. If
the bucket policy is split (S2/S6), these four plus `status/failures/`,
`status/logs/`, `status/warmup/` and `sources/` are reconciler-written.

## A4 frontend — status.json additions (no breaking changes)

- top-level `tick_summary: {seconds, s3_calls, validations, submitted, retried}`
- top-level `tick_seconds` is now the configured value (was hard-coded 300)
- per-volume `terminal: "exit-13" | "capped" | null` — sticky needs-attention
  verdict; clearing it is an operator action (delete the key in
  `status/attempts.json` or bump the pipeline id)
- per-volume `status` may now also be `"deleting"` (Job under Foreground
  deletion) — the `catch("unknown")` in the contract covers it
- per-volume `thumbnail` is a sized IIIF URL **or `null`** — never a direct
  image any more (synthetic/`images:` volumes and service-less manifests are
  `null`); render the placeholder
- per-volume `error` is now populated when one volume's S3/kube effect failed
  during the tick (the row is otherwise intact)
- `source_manifest` for `images:` volumes now has the shape
  `sources/<pipeline>/<volume>/<hash8>/manifest.json`
- `warnings` gains: image allow-list empty; warm-up needs attention; corrupt
  owned JSON treated as absent; per-submission failures

## A2 wrapper — one note

The drift guard now compares `manifest.json.pipeline_sha256` against **both**
the canonical-JSON hash of the parsed steps (`PipelineSpec.steps_sha256`) and
the sha of the ConfigMap text (`legacy_sha256`). The wrapper still publishes
the text sha, which keeps matching. If A2 ever wants to publish the canonical
hash instead, `htrflow_reconciler.parse.canonical_sha256({"steps": ...})` is
the definition (`json.dumps(sort_keys=True, separators=(",", ":"))`).

## B2 docs — what changed in behaviour

- `how-it-works/campaigns`: needs-attention is sticky (attempts.json v2
  `{"n", "terminal"}`); how to clear it; warm-ups share the budget under
  `warmup/<pid>`; retried volumes re-enter the lane the tick **after** the
  delete; `deleting` status; fairness by in-flight count; `images:` edits now
  take effect (new synthetic key).
- `failure-handling.md` / `wrapper.md` / `run-a-volume.md`: the Job now
  really has `podFailurePolicy` (Ignore on DisruptionTarget, FailJob on 13),
  page-derived deadline, and a deadline/SIGTERM kill that made progress is
  resumed without charging an attempt. `backoffLimit` stays 0; TTL stays 24 h.
- `reconciler.md`: env table above; the tick summary log line; the Lease and
  the required RBAC; dulwich instead of git (no `git` binary; `https://` with
  `GIT_TOKEN`); pre-validation is bounded per tick and permanent rejections
  (4xx, non-JSON, over-cap, non-http(s)) are cached forever while transient
  ones back off for `unreachable_ticks × tick_seconds` (3 × 300 s).
- `security`: `RECONCILER_ALLOWED_IMAGE_REPOS` and
  `RECONCILER_REQUIRE_MODEL_REVISION`; `manifest:`/`images:` must be http(s).
- `s3-layout.md`: `status/volumes.json`; synthetic manifest key with hash.

## Deliberately not done in A1

- Kyverno/Sigstore `verifyImages` (A3, optional per plan).
- Model cache PVC rendering (A3; `RECONCILER_DATA_PVC` already read).
- A persisted fairness cursor: the in-flight-count ordering (R5) makes it
  unnecessary; ties fall back to name order.
- `unreachable_ticks` is a `ReconcilerConfig` field (default 3) without an env
  of its own; add `RECONCILER_UNREACHABLE_TICKS` if operators ask.
