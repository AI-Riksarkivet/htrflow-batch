# Evolution

Beyond Phase 2, in rough order of how much new machinery each step adds.

## Frontend + API (v2)

A frontend doesn't need a CRD — it needs an API backend. The natural stack:

```
frontend (submit + monitor UI)
        │ HTTP + auth (OIDC at the ingress)
htrq-api (thin, stateless — htrq's logic as a service)
        │                        │
   k8s API                      S3
   (create Jobs, read       (manifests = durable
   Job/Workload status)      history + live progress)
```

- **`htrq-api`** is `htrq` refactored, not replaced: render-and-apply becomes
  `POST /volumes`, status logic becomes `GET /volumes`. Stateless — cluster +
  S3 *are* the state, keeping the no-database principle.
- **Live per-volume progress is a free payoff of D16 streaming:** the uploader
  ships each ALTO as it's written, so `GET /volumes/{ref}` reports
  `pages_done / pages_total` by listing S3 keys — a progress bar per running
  volume with zero new plumbing.
- **History:** completed volumes survive Job TTL forever via `manifest.json`
  (per pipeline id). Gap: *failure* history past the 7-day TTL — v2 answer:
  lengthen TTL or have the API archive termination messages to S3; a real
  database only if failure analytics demand it.
- **Frontend v1 scope:** submit form (reference codes, pipeline dropdown = the
  chart's pipeline ConfigMap list, priority), queue table (warming/queued/
  running/done/failed with termination-log reasons), volume detail with
  progress + links into the viewer (below). Before any of this exists, Kueue's
  dashboard (kueueviz) + `htrq` covers ops visibility.
- **Viewer (D19):** the Riksarkivet **universalviewer4 fork** is already an
  HTR viewer — TextRightPanel renders ALTO from canvas `seeAlso`, line
  overlays sync with the OpenSeadragon canvas, SearchLeftPanel auto-hides
  without a SearchService. Deploy = static build in an nginx pod (NodePort);
  one host page reads `#?manifest=<url>`, so one deployment serves every
  volume: `http://<node>:<port>/#?manifest=<PUBLIC_RESULTS_BASE>/<pipeline>/
  <volume>/iiif.json`. `htrq view <ref>` prints that URL. Optional later:
  IIIF Content Search 1.0 shim (could be backed by the rask lines FTS) to
  light up the search panel — 1–2 days, separate item.

## CRD guidance (v3, only if demanded)

Decided **against** any CRD for the PoC (D18): everything a `Transcription` CR
would own is already owned by cheaper primitives (Kueue = queueing, Job =
lifecycle, deterministic names = idempotency, ConfigMaps = pipelines), and a
controller is a standing distributed-systems component added to a PoC.

If/when a second **machine** consumer (rask orchestrator, GitOps campaigns)
needs a declarative contract — the API service above hides the switch:

- **CR per campaign, not per volume.** Archive scale means hundreds of
  thousands of volumes; that many CRs in etcd (~8 Gi practical ceiling,
  watch-cache pressure) is a known anti-pattern. The campaign CR's spec holds
  the volume list (or a pointer to it); status aggregates counts. Per-volume
  truth stays where it already is: `manifest.json` in S3.
- The reconciler creates exactly the Jobs specced in [the Job template](../how-it-works/wrapper.md#job-template-one-volume-one-job)
  — the Kueue layer and wrapper never change; `htrq`'s render logic becomes
  the controller's guts.
- Same ladder applies to pipelines ([Pipeline configs](../how-it-works/wrapper.md#pipeline-configs-d17)):
  a `HtrPipeline` CRD only for admission-time validation + auto-warm-up, and
  only at v3.

## Other items

- **Intra-volume sharding** — Indexed Job / JobSet page ranges for
  latency-critical volumes; requires an assembly step (excluded now).
- **Small-volume batching** — the per-Job model load (~30–60 s) is noise for
  volumes of hundreds of pages but ~50 % overhead for a 10-page volume; if
  tiny volumes become common, `htrq` groups volumes under ~50 pages into one
  Job (one model load, N volumes through the resident pipeline, still one
  `manifest.json` each) — a relaxation of D3, not a redesign.
- **Cohorts/borrowing** — share idle blackwell capacity via Kueue cohorts once
  coordinated with the Gemma deployment.
- **rask integration** — the orchestrator submits via the API above (or the
  campaign CR); wrapper and queueing unchanged.
- **Metrics** — Kueue ships Prometheus metrics; wrapper adds pages/sec to
  `manifest.json` today, a push gateway later if needed.
- **Upstream fix** — PR to htrflow collecting executor futures in `cli.py` so
  page failures propagate to the exit code (D16 sidesteps this in-process, but
  CLI-mode fallbacks L1/L2 and other users still benefit).
