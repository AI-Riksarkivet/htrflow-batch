# Audit remediation plan — 2026-08-26

Source: `docs/audits/2026-08-26-repo-audit.md` (all findings, per-angle IDs). Goal: fix every
finding that is fixable in this repository; anything that is an operator/infra decision is
turned into a chart option or a documented step, never silently dropped.

## Work packages (phase A runs in parallel, each in its own worktree)

| WP | Owner scope (files) | Findings |
|---|---|---|
| A1 reconciler | `packages/reconciler/**` | R1–R14, S1 (allow-list, revision), S4/S5 (scheme + size caps in pre-validation), O2 (deadline from pages, podFailurePolicy, node selector/tolerations in `jobspec.py`), O5 (tick summary log, `TICK_SECONDS` env), O7 (git timeout ≤ deadline), O8 (Lease), F2 upstream (sized thumbnails; `null` for service-less), X1 tick cost |
| A2 wrapper | `packages/wrapper/**`, `.docker/htrflow-batch*.dockerfile` | W1–W13, O2 (SIGTERM handler), S5 (byte caps), S6 (URL redaction in logs), W8/S7 (pins) |
| A3 chart / ops | `charts/**`, `scripts/**`, `Makefile`, `.docker/uv4-viewer.dockerfile` if needed | O4, O6, O7, O8 (RBAC), O10–O18, S2 (bucket policy split, no console NodePort by default), S3 (digest required for control-plane images), S8, S9, S4 (security headers on nginx), Kyverno verifyImages template (optional, off by default) |
| A4 frontend | `frontend/**` | F1–F16, S4 (scheme validation, SvelteKit `kit.csp`) |
| B1 CI / tests | `.dagger/**`, `.github/**`, `pyproject.toml`, `packages/*/tests` additions, `renovate.json` | T1–T14 (after A merges) |
| B2 docs | `docs/**`, `README.md`, `charts/**/README.md` | D-H1–H4, D-M1–M12, D-L1–L10, F16, T10 (after everything) |

## Contracts (agreed up front so packages don't drift)

### Reconciler env (chart renders; reconciler `Settings` reads)
| Env | Chart value | Default | Purpose |
|---|---|---|---|
| `RECONCILER_TICK_SECONDS` | `reconciler.tickSeconds` | 300 | STALE threshold; must match `reconciler.schedule` |
| `RECONCILER_DATA_PVC` | `modelCache.name` | `htr-test-data` | model cache PVC (chart renders it when `modelCache.create`) |
| `RECONCILER_ALLOWED_IMAGE_REPOS` | `security.allowedImageRepos` (list → comma) | `""` = any (warning emitted) | pipeline image allow-list (repo prefix match, before `@sha256:`) |
| `RECONCILER_REQUIRE_MODEL_REVISION` | `security.requireModelRevision` | `false` | every `model_settings.model` needs a 40-hex `revision` |
| `RECONCILER_JOB_MIN_DEADLINE_SECONDS` | `job.minDeadlineSeconds` | 21600 | Job `activeDeadlineSeconds = max(min, pages × perPage)` |
| `RECONCILER_JOB_SECONDS_PER_PAGE` | `job.secondsPerPage` | 30 | see above |
| `RECONCILER_JOB_RUNTIME_CLASS` | `job.runtimeClassName` | `nvidia` | |
| `RECONCILER_JOB_NODE_SELECTOR` | `job.nodeSelector` (JSON) | `{}` | |
| `RECONCILER_JOB_TOLERATIONS` | `job.tolerations` (JSON) | `[]` | |
| `RECONCILER_MAX_VALIDATIONS_PER_TICK` | `reconciler.maxValidationsPerTick` | 50 | bounded pre-validation |
| `RECONCILER_FETCH_MAX_BYTES` | `reconciler.fetchMaxBytes` | 16777216 | manifest byte cap |
| `RECONCILER_LEASE_NAME` | — | `htr-reconciler` | per-tick Lease; RBAC `coordination.k8s.io/leases get,create,update` |

CronJob: `startingDeadlineSeconds: 120`; `activeDeadlineSeconds` = `reconciler.tickDeadlineSeconds` (default 600, ≥ git clone timeout).

### Job spec (built by `jobspec.py`; `job-example.yaml` mirrors)
`backoffLimit: 0` stays; add `podFailurePolicy: [{action: Ignore, onPodConditions: [{type: DisruptionTarget}]}, {action: FailJob, onExitCodes: {containerName: wrapper, operator: In, values: [13]}}]`.
Requests stay cpu 4 / 8Gi / 1 GPU; chart `queue.resources` defaults must admit that.

### S3 state
`attempts.json` v2: `{"<pid>/<vid>": {"n": int, "terminal": "exit-13" | "capped" | null}}`; ints from v1 are migrated on read. A terminal record makes `derive` return `needs-attention` regardless of Job presence; clearing it is an operator action (delete the key or bump the pipeline id).
Thumbnail: sized IIIF URL (`/full/200,/0/default.jpg`) when a service exists; `null` otherwise (service-less canvases and synthetic manifests). Frontend shows a neutral placeholder.
Synthetic manifests: key includes a short hash of the image list; edits in git therefore take effect.

### status.json
No breaking changes. Additions: top-level `tick_summary` `{seconds, s3_calls, validations, submitted, retried}`; per-volume `terminal` (string|null). Unknown volume statuses must not break the frontend (`catch("unknown")`).

### Wrapper env
`FETCH_MAX_BYTES` (default 67108864), `MANIFEST_MAX_BYTES` (16777216). Exit codes: 13 only for 4xx / non-JSON manifest, bad pipeline config, empty manifest; network errors and 5xx are transient. SIGTERM → termination log `{"stage": ..., "permanent": false, "error": "SIGTERM"}` + final log ship + exit 143.
Upload order per page: `page` then `alto`; verify lists both; `manifest.json` gains `page_sources: {name: image_url}` and resume compares them.

### Frontend
`status.ts`: every URL field validated `http(s)` (invalid → null + warning row); `volumeStatusSchema.catch("unknown")`. `kit.csp` in `svelte.config.js` (mode `hash`, `script-src 'self'`, `object-src 'none'`, `base-uri 'self'`).

## Rules for every package
TDD (failing test first), ruff format + ruff check + ty clean, full package suite green, small commits with why-messages, no `Co-Authored-By`, no pushes, no cluster mutations, don't edit files outside your scope (write a `HANDOFF.md` note in your worktree root for anything another package must do). Report: what was fixed (finding IDs), what was deliberately not (with reason), test counts before/after.
