# Evolution

Beyond Phase 2, in rough order of how much new machinery each step adds.
What exists today: the GitOps reconciler and the read-only campaign browser
([Campaigns (GitOps)](../how-it-works/campaigns.md)); everything below is a
proposal.

## `htrq` CLI (proposal, not built)

The original PoC design (D7) submitted volumes with a small Python/Typer
tool and no in-cluster components. The GitOps reconciler superseded it for
campaigns — a commit is the submission, and the reconciler owns naming,
idempotency, retries and status. What a CLI would still add is the
*hand-run* path: one volume, right now, without a commit. If it is ever
built, the reconciler's modules are its guts (`jobspec.build_job`,
`status.job_name`, `parse`), and its surface would be:

- `htrq submit <ref>...` — resolve reference code → IIIF manifest URL
  (Riksarkivet IIIF collection API), render the Job from `jobspec`,
  `kubectl apply`. Deterministic names make duplicates a clean API-server
  conflict; `--force` = delete-then-apply; `--priority` selects a lane
  (D13); `--pipeline` selects the ConfigMap and sets `PIPELINE_ID`.
- `htrq submit --dry-run` — resolve the manifest, print page count +
  estimated runtime + Job YAML without applying (D15).
- `htrq status [<ref>]` — queued (suspended) / running / succeeded / failed,
  with Kueue workload position and termination-log reasons. For campaign
  volumes `status.json` already answers this.
- `htrq logs <ref>`, `htrq retry <ref>` — kubectl conveniences. For campaign
  volumes the run log is in S3 and a retry is clearing the attempts record.
- `htrq report` — aggregate GPU stall fraction (`gpu_stall_seconds /
  wall_seconds`) and throughput across recent `manifest.json`s: the Phase 2
  evidence in one command. Nothing computes this today; it is a script over
  the bucket away.
- `htrq pipeline deploy <yaml>` / `pipeline list` — validate, create the
  immutable ConfigMap, run the warm-up. The reconciler does all three for
  campaigns-repo pipelines; `make warmup` covers chart-declared ones.

Hand-run Jobs should keep `app=htrflow-batch` without
`batch.htrflow/managed-by=reconciler`, so they never count against the
reconciler's window.

## Frontend + API (v2)

The campaign browser is read-only by design. A *submitting* frontend needs
an API backend, not a CRD:

```
frontend (submit + monitor UI)
        │ HTTP + auth (OIDC at the ingress)
thin API (stateless — the reconciler's parse/jobspec/status as a service)
        │                        │
   k8s API                      S3
   (create Jobs, read       (manifests = durable
   Job/Workload status)      history + live progress)
```

- The API is the reconciler's modules refactored, not replaced:
  render-and-apply becomes `POST /volumes`, `derive` becomes
  `GET /volumes`. Stateless — cluster + S3 *are* the state, keeping the
  no-database principle. (For campaigns the API could simply commit to the
  campaigns repo.)
- **Live per-volume progress is a free payoff of D16 streaming:** the
  uploader ships each page as it's written and the run log every 15 s, so
  `pages_done / pages_total` and the live log are already in `status.json`
  today — an API adds nothing here.
- **History:** completed volumes survive Job TTL forever via `manifest.json`
  (per pipeline id). Failure history survives too: `status/failures/…`,
  `metrics-failed-latest.json` and the sticky `terminal` verdict in
  `attempts.json` outlive the 24 h Job TTL. A real database only if failure
  analytics demand it.
- **Frontend v1 scope:** submit form (reference codes, pipeline dropdown =
  the deployed pipeline list, priority), queue table, volume detail with
  progress + links into the viewer (below). The campaign browser already
  covers the monitoring half.
- **Viewer (D19):** the Riksarkivet **universalviewer4 fork** is already an
  HTR viewer — TextRightPanel renders ALTO from canvas `seeAlso`, line
  overlays sync with the OpenSeadragon canvas, SearchLeftPanel auto-hides
  without a SearchService. Deploy = static build in an nginx pod (NodePort);
  one host page reads `#?manifest=<url>`, so one deployment serves every
  volume: `http://<node>:<port>/uv.html#?manifest=<PUBLIC_RESULTS_BASE>/<pipeline>/
  <volume>/iiif.json` — the campaign browser links it per volume. Optional
  later: IIIF Content Search 1.0 shim (could be backed by the rask lines
  FTS) to light up the search panel — 1–2 days, separate item.

## CRD guidance (v3, only if demanded)

Decided **against** any CRD for the PoC (D18): everything a `Transcription`
CR would own is already owned by cheaper primitives (Kueue = queueing, Job =
lifecycle, deterministic names = idempotency, ConfigMaps = pipelines, git =
desired state), and the one controller-shaped component — the reconciler —
is a stateless CronJob, not a standing watch loop.

If/when a second **machine** consumer (rask orchestrator) needs a
declarative contract inside the cluster rather than a git repo:

- **CR per campaign, not per volume.** Archive scale means hundreds of
  thousands of volumes; that many CRs in etcd (~8 Gi practical ceiling,
  watch-cache pressure) is a known anti-pattern. The campaign CR's spec holds
  the volume list (or a pointer to it); status aggregates counts. Per-volume
  truth stays where it already is: `manifest.json` in S3.
- The controller creates exactly the Jobs `jobspec.build_job` builds today
  — the Kueue layer and wrapper never change; the reconciler's tick becomes
  the controller's reconcile.
- Same ladder applies to pipelines ([Pipeline configs](../how-it-works/wrapper.md#pipeline-configs-d17)):
  a `HtrPipeline` CRD only for admission-time validation + auto-warm-up, and
  only at v3.

## Other items

- **Two S3 principals** — today one credential serves the reconciler and
  every Job. Job credentials scoped to `<pipeline>/<volume>/*` plus the
  run-log key, and reconciler credentials to `status/*` and `sources/*`,
  need IAM users/policies created at bucket init and a second Secret in
  `jobspec.py` ([Security](../development/security.md#trust-boundary)).
- **Intra-volume sharding** — Indexed Job / JobSet page ranges for
  latency-critical volumes; requires an assembly step (excluded now).
- **Small-volume batching** — the per-Job model load (~30–60 s) is noise for
  volumes of hundreds of pages but ~50 % overhead for a 10-page volume; if
  tiny volumes become common, the reconciler groups volumes under ~50 pages
  into one Job (one model load, N volumes through the resident pipeline,
  still one `manifest.json` each) — a relaxation of D3, not a redesign.
- **Cohorts/borrowing** — share idle blackwell capacity via Kueue cohorts once
  coordinated with the Gemma deployment.
- **rask integration** — the orchestrator commits to the campaigns repo, or
  submits via the API above; wrapper and queueing unchanged.
- **Metrics** — Kueue ships Prometheus metrics; the reconciler logs one
  `tick:` summary line and publishes `tick_summary` in `status.json`; a
  push gateway or kube-state-metrics alerts on the CronJob later if needed.
- **Purpose-built git-daemon image** — so the devStack git daemon can drop
  root and `security.psaEnforce` can become `restricted`.
- **Upstream fix** — PR to htrflow collecting executor futures in `cli.py` so
  page failures propagate to the exit code (D16 sidesteps this in-process, but
  CLI-mode fallbacks L1/L2 and other users still benefit).
