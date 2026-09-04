# Evolution

Beyond Phase 2, in rough order of how much new machinery each step adds.
What exists today: campaigns as Kubernetes Indexed Jobs rendered by a pure
converter, and a read-only web front over the live cluster
([Campaigns (Indexed Jobs)](../how-it-works/campaigns.md)); everything below
is a proposal.

## `htrq` CLI (proposal, not built)

The original PoC design (D7) submitted volumes with a small Python/Typer
tool and no in-cluster components. Campaigns in git superseded it for the
bulk path — a commit is the submission, and Kubernetes owns naming,
idempotency and retries. What a CLI would still add is the *hand-run* path:
one volume, right now, without a commit. If it is ever built,
`packages/converter`'s modules are its guts (`parse`, `models`, `render`),
and its surface would be:

- `htrq submit <ref>...` — resolve reference code → IIIF manifest URL
  (Riksarkivet IIIF collection API), render a one-index Job the way
  `render._campaign_job` does, apply it. `--priority` selects a lane (D13);
  `--pipeline` selects the ConfigMap and sets `PIPELINE_ID`.
- `htrq submit --dry-run` — resolve the manifest, print page count +
  estimated runtime + Job YAML without applying (D15). `htrflow-campaigns
  apply --dry-run` prints the objects but never reads a manifest, so the
  page count and the estimate are the new part.
- `htrq status [<ref>]` — queued (suspended) / running / succeeded / failed,
  with Kueue workload position and termination-log reasons. For campaign
  volumes `GET /api/v1/jobs/{ns}/{name}` already answers this, and the
  campaign browser renders it.
- `htrq logs <ref>`, `htrq retry <ref>` — kubectl conveniences. For campaign
  volumes the run log is in S3 at a deterministic key and a retry is
  Kubernetes' own `backoffLimitPerIndex`, with nothing to clear by hand.
- `htrq report` — aggregate GPU stall fraction (`gpu_stall_seconds /
  wall_seconds`) and throughput across recent `manifest.json`s: the Phase 2
  evidence in one command. Nothing computes this today; it is a script over
  the bucket away.
- `htrq pipeline deploy <yaml>` / `pipeline list` — validate, create the
  ConfigMap, run the warm-up. `htrflow-campaigns` does all three for a
  campaigns repo (`validate`, then `render`, which emits the pipeline
  ConfigMap and its warm-up Job); a CLI would do it for a pipeline that is
  not in one yet.

Hand-run Jobs should keep `app=htrflow-batch` (so the NetworkPolicies apply)
without `htrflow.riksarkivet.se/managed-by=converter`, so they are neither
listed as a campaign by the read API nor deleted by
`htrflow-campaigns apply --prune`.

## Frontend + API (v2)

The campaign browser is read-only by design, and so is the API under it
(`packages/web`: get/list/watch on Jobs, Pods and ConfigMaps, nothing more).
A *submitting* frontend needs a write path, not a CRD:

```
frontend (submit + monitor UI)
        │ HTTP + auth (OIDC at the ingress)
thin API (stateless — the converter's parse/render as a service,
          plus today's read-only projection)
        │                        │
   k8s API                      S3
   (create Jobs, read       (manifests = durable
   Job/Workload status)      history)
```

- The write half is `packages/converter` behind HTTP: render-and-apply
  becomes `POST /campaigns`. The read half already exists and does not
  change. Stateless — cluster + S3 *are* the state, keeping the
  no-database principle. (For campaigns the API could equally commit to the
  campaigns repo and let the existing loop apply it.)
- **Live per-volume progress is a free payoff of D16 streaming:** the
  uploader ships each page as it is written and the run log every 15 s, so
  progress is already visible without anything deriving or storing it — the
  Job's `completedIndexes` and the live log are the whole story. An API adds
  nothing here.
- **History:** completed volumes survive the Job's 24 h TTL forever via
  `manifest.json` (per pipeline id). Failure history does *not*: once the
  Job is reaped, the run log at `status/logs/<pipeline>/<volume>.txt` is all
  that is left of a failed attempt, and a later attempt overwrites it. A
  durable failure record — or a real database — only if failure analytics
  demand it.
- **Frontend v1 scope:** submit form (reference codes, pipeline dropdown =
  the deployed pipeline list, priority), queue table, volume detail with
  progress + links into the viewer (below). The campaign browser already
  covers the monitoring half.
- **Viewer (D19):** the Riksarkivet **universalviewer4 fork** is already an
  HTR viewer — TextRightPanel renders ALTO from canvas `seeAlso`, line
  overlays sync with the OpenSeadragon canvas, SearchLeftPanel auto-hides
  without a SearchService. It is built into the `htrflow-web` image and
  served by the same process as the API and the SPA; one host page reads
  `#?manifest=<url>`, so one deployment serves every volume:
  `http://<node>:<port>/uv.html#?manifest=<PUBLIC_RESULTS_BASE>/<namespace>/<pipeline>/<volume>/iiif.json`
  — the campaign browser links it per volume. Optional later: IIIF Content
  Search 1.0 shim (could be backed by the rask lines FTS) to light up the
  search panel — 1–2 days, separate item.

## CRD guidance (v3, only if demanded)

Decided **against** any CRD (D18), and B63 removed the last
controller-shaped component with it: everything a `Transcription` CR would
own is already owned by cheaper primitives (Kueue = queueing, the Indexed
Job = lifecycle and retries, `completions` = the volume list, ConfigMaps =
pipelines, git = desired state). There is nothing left in-cluster that
watches, ticks or reconciles.

If/when a second **machine** consumer (rask orchestrator) needs a
declarative contract inside the cluster rather than a git repo:

- **CR per campaign, not per volume.** Archive scale means hundreds of
  thousands of volumes; that many CRs in etcd (~8 Gi practical ceiling,
  watch-cache pressure) is a known anti-pattern. The campaign CR's spec
  holds the volume list (or a pointer to it); status aggregates counts.
  Per-volume truth stays where it already is: `completedIndexes` on the Job
  while it lives, `manifest.json` in S3 forever.
- The controller creates exactly the objects `render.campaign_objects`
  builds today — the Kueue layer and the wrapper never change; the
  converter becomes a library the controller calls instead of a CLI CI runs.
  A campaign is already one object, so this is a thinner step than it was
  when a campaign meant N per-volume Jobs.
- Same ladder applies to pipelines ([Pipeline configs](../how-it-works/wrapper.md#pipeline-configs-d17)):
  a `HtrPipeline` CRD only for admission-time validation + auto-warm-up, and
  only at v3. Note that the *policy* half of that argument is already
  answered without a CRD: the chart's Kyverno ClusterPolicies validate
  pipeline ConfigMaps at admission
  ([Security → Trust boundary](../development/security.md#trust-boundary)).

## Other items

- **Two S3 principals** — resolved by removal, not by building it: the web
  front never touches S3 (it is a Kubernetes API client with read-only
  RBAC), so the only S3-credentialed pods left are the campaign and warm-up
  pods. Scoping *their* credential to
  `<namespace>/<pipeline>/<volume>/*` plus the run-log key would still be an
  improvement, and needs IAM users/policies created at bucket init and a
  second Secret named in `converter.yaml`
  ([Security](../development/security.md#trust-boundary)).
- **Intra-volume sharding** — page ranges across several indexes for
  latency-critical volumes; requires an assembly step and breaks the
  one-index-one-volume contract the whole read path assumes (excluded now).
- **Small-volume batching** — the per-Job model load (~30–60 s) is noise for
  volumes of hundreds of pages but ~50 % overhead for a 10-page volume; if
  tiny volumes become common, the converter could pack volumes under ~50
  pages into one index (one model load, N volumes through the resident
  pipeline, still one `manifest.json` each) — a relaxation of D3, and a
  change to the wrapper's per-index contract, not a redesign of the queue.
- **Cohorts/borrowing** — share idle blackwell capacity via Kueue cohorts once
  coordinated with the Gemma deployment.
- **rask integration** — the orchestrator commits to the campaigns repo, or
  submits via the API above; wrapper and queueing unchanged.
- **Metrics** — Kueue ships Prometheus metrics and the Job's own
  `completedIndexes`/`failedIndexes` are already scrapeable via
  kube-state-metrics; the platform itself publishes none. Story B40.
- **Kyverno `ValidatingPolicy` migration** — Kyverno 1.19 deprecates the
  `kyverno.io/ClusterPolicy` kind the chart ships in favour of CEL-based
  `policies.kyverno.io/ValidatingPolicy`, and warns on every apply. A task
  of its own ([Chart Values](../reference/chart.md#trust-boundary-security)).
- **Upstream fix** — PR to htrflow collecting executor futures in `cli.py` so
  page failures propagate to the exit code (D16 sidesteps this in-process, but
  CLI-mode fallbacks L1/L2 and other users still benefit).
