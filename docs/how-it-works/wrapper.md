# The Wrapper

## `htrflow-batch` image and wrapper

`FROM <registry>/htrflow:<tag>` (pinned by digest) + `batch_run.py`
(~250 lines) + `httpx`, `boto3`.

**The wrapper is a streaming driver (D16), not a CLI shell-out.** It imports
htrflow as a library — `Pipeline.from_config()` once at startup (models load
once) — then runs a producer–consumer pipeline with three concurrent roles:

| Role | What it does |
|---|---|
| **downloader pool** (async, 8–16 in flight, bounded lookahead ≤ `LOOKAHEAD_PAGES`) | fetches pages in manifest order into tmpfs; per-page retry with backoff; enqueues each page as it lands |
| **consumer** (single thread — the GPU serializes work anyway) | `pipeline.run(document)` per page, in order, the moment the page is available; holds each page's result/exception directly (fixes the [known upstream flaw](decision-log.md#known-upstream-flaw-the-design-must-absorb) at the source) |
| **uploader** | ships each ALTO/PAGE to S3 the moment htrflow writes it (deterministic keys, blind overwrite); rolling-deletes the source image once its page is done |

Net effect: GPU idle ≈ one page's download time; results stream into S3
progressively (a 6-hour volume shows live progress); tmpfs holds only the
lookahead window, never the whole volume.

Because the library API — unlike the CLI — is not a stability contract, the
image digest pin is load-bearing: the wrapper is validated against the exact
htrflow version in the image ([library-API pin test](../development/testing.md)).
Fallback modes if the API proves awkward at a version bump:

- **L1 — stock CLI + watcher-uploader:** download-all-then-run, uploader thread
  streams outputs as they're written. Streaming out only.
- **L2 — chunked CLI invocations:** ~100-page chunks, download chunk N+1 while
  chunk N processes; costs a model reload (~30–60 s GPU idle) per chunk.

### Wrapper contract — env vars

| Env | Meaning | Default |
|---|---|---|
| `VOLUME_REF` | archival reference code (S3 prefix, logging) | required |
| `IIIF_MANIFEST_URL` | manifest to process (resolved by CLI at submit time) | required |
| `PIPELINE_PATH` | pipeline YAML, mounted from the immutable per-version ConfigMap | required |
| `PIPELINE_ID` | short id namespacing the output keys | required |
| `S3_BUCKET` | result destination bucket (creds from Secret) | required |
| `PUBLIC_RESULTS_BASE` | browser-reachable base URL for `iiif.json`/viewer links (≠ the in-cluster S3 endpoint) | required |
| `S3_ENDPOINT` | S3 endpoint URL (empty = provider default chain) | "" |
| `S3_PREFIX` | key prefix under the bucket | "" |
| `MAX_IMAGE_WIDTH` | IIIF size cap (`/full/{w},/`) — **enforced**, and part of the fetched URL, so cached/stored artifacts can never disagree with config (note: `!w,h` 501s on lbiiif) | 2500 |
| `RESUME` | skip pages whose outputs already exist | true |
| `LOOKAHEAD_PAGES` | max pages downloaded ahead of the consumer (bounds tmpfs) | 64 |
| `MAX_PAGES` | cap on pages processed, `0` = all (test knob) | 0 |
| `WORKDIR_PATH` | filesystem path for downloads + local pipeline outputs | /work |
| `DOWNLOAD_CONCURRENCY` | concurrent image downloads | 12 |
| `TERMINATION_LOG_PATH` | where the exit reason (stage, permanent/transient, error) is written | /dev/termination-log |

Required: `VOLUME_REF`, `IIIF_MANIFEST_URL`, `PIPELINE_PATH`, `PIPELINE_ID`,
`S3_BUCKET`, `PUBLIC_RESULTS_BASE` — everything else is optional with the
default shown above (per `packages/wrapper/src/htrflow_batch/config.py`; this
corrects an earlier design-doc draft that also marked `S3_ENDPOINT` and
`S3_PREFIX` required).

### Stages around the streaming loop

1. **setup** — fetch IIIF manifest, enumerate canvases → ordered page list,
   zero-padded filenames (empty/bad manifest → exit 13); start the downloader
   pool; **then** `Pipeline.from_config($PIPELINE_PATH)` — model load overlaps
   the first pages' downloads, so startup GPU-idle is
   `max(model_load, first_page_download)`, not the sum (see
   [Model handling](#model-handling)).
2. **resume** — list existing per-page outputs in S3; drop done pages
   (`RESUME=false` forces full reprocessing). Skipped pages are never downloaded.
3. **streaming loop** — downloader ∥ consumer ∥ uploader as above; per-page
   failures (download after retries, or an exception from `pipeline.run`) are
   recorded, not fatal mid-loop — the loop drains what it can first.
4. **verify (D8)** — every page accounted for: a result held by the consumer
   AND its uploads confirmed. Any gap → transient exit (Job retry + resume
   converges); the missing-page list goes in the termination message.
5. **publish** — after a clean verify: `iiif.json` (viewer manifest, D19),
   then `manifest.json` **last** (still the sole completion marker). All
   uploads carry real content-types (`application/xml` for ALTO,
   `application/json` for manifests) — blind `put_object` defaults to
   octet-stream, which breaks browsers.

### Exit codes

| Code | Meaning | Job reaction |
|---|---|---|
| 0 | success (verified) | Complete |
| 13 | permanent (bad manifest URL, bad pipeline YAML, volume exceeds budget) | `FailJob` — no retry |
| other | transient (network, CUDA hiccup, verification gap) | retry within `backoffLimit` |

Failures write a structured reason to `/dev/termination-log`
(`{"stage": "fetch", "page": 412, "error": ...}`) so `htrq status` shows *why*
without log spelunking.

**Instrumentation for the Phase 2 gate:** `manifest.json` records `pages`,
`bytes_fetched`, `wall_seconds`, per-page timings, and — the key metric —
`gpu_stall_seconds`: total time the consumer sat waiting on an empty page
queue. Stall fraction = `gpu_stall_seconds / wall_seconds`, aggregated over
the first real campaign, decides whether Phase 2 exists (see
[Phase 2: Cache Layer](../roadmap/phase-2-cache.md)). With streaming,
expected stall ≈ first page's download + any moments IIIF falls behind the GPU.

## Kueue topology

Namespace `htr-batch`. Standard three objects:

```yaml
apiVersion: kueue.x-k8s.io/v1beta2
kind: ResourceFlavor
metadata:
  name: gpu-ada
spec:
  nodeLabels:
    gpu-group: ada          # HTR owns ada; Gemma owns blackwell
---
apiVersion: kueue.x-k8s.io/v1beta2
kind: ClusterQueue
metadata:
  name: htr-batch-cq
spec:
  namespaceSelector: {}
  resourceGroups:
  - coveredResources: [cpu, memory, nvidia.com/gpu]
    flavors:
    - name: gpu-ada
      resources:
      - name: nvidia.com/gpu
        nominalQuota: 2       # tunable: max concurrent volumes
      - name: cpu
        nominalQuota: 8
      - name: memory
        nominalQuota: 32Gi    # 2 × 16 Gi pods — streaming keeps pods small
---
apiVersion: kueue.x-k8s.io/v1beta2
kind: LocalQueue
metadata:
  name: htr-batch
  namespace: htr-batch
spec:
  clusterQueue: htr-batch-cq
```

Jobs carry `kueue.x-k8s.io/queue-name: htr-batch`, start `suspend: true`;
Kueue unsuspends as quota frees. Submit 200 volumes → exactly N run, the rest
wait in FIFO order (`kubectl get workloads -n htr-batch`).

No preemption, no cohorts in Phase 1 — first knobs to turn when sharing with
other tenants.

## Job template (one volume = one Job)

- Single container, `restartPolicy: Never`.
- Resources: 1 GPU, ~4 CPU, ~16 Gi memory (tmpfs counts against this — see
  [Memory Budget](memory-budget.md)).
- `backoffLimit: 2`; `podFailurePolicy`: ignore pod **disruptions** (node drain
  doesn't burn a retry), `FailJob` on exit 13 — now safe to wire after commit
  `af8df6a` (the wrapper validates the pipeline YAML up front, so only
  file/parse errors map to exit 13; an `OSError` out of `from_config`'s HF
  model downloads stays transient).
- `activeDeadlineSeconds: 21600` (6 h runaway guard),
  `ttlSecondsAfterFinished: 604800` (7 d — inspectable, then self-cleans).
- Labels `app=htrflow-batch`, `batch.htrflow/volume=<slug>`; full reference
  code in an annotation (reference codes aren't label-safe).
- Job name `htr-<slug>-<hash(ref + pipeline_id)>` — deterministic, so duplicate
  submission collides at the API server: **the cluster enforces idempotent
  submission, not CLI bookkeeping** (D10).
- Mounts: pipeline ConfigMap (immutable, per version — see
  [Pipeline configs](#pipeline-configs-d17)), S3 Secret as a credentials
  *file*, tmpfs workdir, read-only model cache (see
  [Model handling](#model-handling)); Pod Security `restricted`
  ([Security](../development/security.md)).

## Output store and completion contract

S3 (HCP) behind a one-function seam — `publish(volume, files)`:

- Key layout: `s3://$BUCKET/$PREFIX/<pipeline-id>/<volume-ref>/...` —
  **pipeline id in the key**, so reprocessing with a better model is a new
  namespace, never an overwrite of the previous campaign's results.
- Per-page keys, deterministic, blind overwrite → retries converge.
- `manifest.json` uploaded **last**; its presence *is* "volume complete"
  (for that pipeline id).
- Contents (D11): page count, canvas→page mapping with source IIIF URLs,
  pipeline YAML content + hash, htrflow version, batch image digest, HF model
  revisions resolved at runtime, `fetch_seconds`/`htr_seconds`/pages/sec.
  The pipeline YAML itself is uploaded alongside.
- NFS alternative: same contract via write-temp + atomic rename; swap the
  `publish()` implementation only.
- **Viewer manifest `iiif.json` (D19):** IIIF Presentation 3, one canvas per
  page — image service copied from the source lbiiif canvas (tiles keep coming
  from lbiiif; we serve no images), canvas width/height = the **width-capped
  dimensions actually processed** (read back from the ALTO `<Page>` — keeps
  the UV line overlays aligned without coordinate rewriting), and per-canvas
  `seeAlso: [{id: <public ALTO URL>, profile: ".../alto/ns-v4#"}]` — the exact
  shape the UV fork's TextRightPanel matches on. Needs env
  `PUBLIC_RESULTS_BASE` (browser-reachable URL base, ≠ the in-cluster S3
  endpoint). Written after verify, keyed under the same
  `<pipeline-id>/<volume-ref>/` prefix — reprocessed campaigns get their own
  viewer manifests.
- **Store requirements for the viewer:** anonymous read on the results
  prefix + CORS (GET from the viewer origin) — the browser fetches manifest
  and ALTO directly.
- **UV4-fork gotchas (found deploying the viewer, 2026-07-28, see the
  [test log](../development/test-log.md)):**
  - The ALTO text panel is gated on `manifest.getSearchService()` — a
    manifest without a IIIF search service never shows transcriptions.
    The wrapper therefore emits a **stub SearchService1 entry** (endpoint
    not implemented; only its presence matters). Replace with a real
    content-search service if one ever exists.
  - Canvases need an explicit `thumbnail` property when the image body has
    no IIIF image service (UV renders empty thumbs otherwise). The wrapper
    emits `{service}/full/200,/0/default.jpg` when a service exists
    (width syntax — lbiiif 501s on `!w,h`), else the full static image.
  - The fork's shipped `uv.html` never fetches `uv-iiif-config.json` (the
    fetch is commented out) and `textRightPanelEnabled` is **not** compiled
    into `UV.js` — the panel can't turn on without patching the page.
  - The fork feeds raw ALTO pixel coords to OpenSeadragon as **viewport**
    coords → line overlays land ~10⁵ px off-canvas for plain images; fix is
    `viewport.imageToViewportRectangle(...)`. Both fixes captured in
    `.docker/uv4-uv-html.patch` (built as image `uv4:v3`; PR-worthy
    upstream).
- **Live run log:** the wrapper tees its own stdout/stderr and re-uploads
  the buffer to `status/logs/<pipeline-id>/<volume-ref>.txt` every
  `LOG_SHIP_SECONDS` (15) plus once on exit. That is how the frontend follows
  a running volume without anything ever reading the kube API from a browser:
  the reconciler links the key as soon as it exists, and the run viewer polls
  it while the volume is in flight. See the
  [live run log spec](../superpowers/specs/2026-08-26-live-run-log-design.md).
- The results bucket is the **only stateful dependency** in the system —
  and it should be the durable HCP, not the volatile `/tmp`-backed dev MinIO
  (viewer links must not die on reboot).
- Honest limit: with Job TTL at 7 d, long-term "what has been processed?" is
  answered by listing `manifest.json` keys in S3 — acceptable for the PoC,
  revisit if it becomes a frequent operational question.

## `htrq` CLI

Small Python/Typer tool, no in-cluster components:

- `htrq submit <ref>...` — resolve reference code → IIIF manifest URL
  (Riksarkivet IIIF collection API), render Job from template, `kubectl apply`.
  Deterministic names make duplicates a clean API-server conflict; `--force` =
  delete-then-apply; `--priority` selects lane (D13); `--pipeline` selects the
  ConfigMap entry and sets `PIPELINE_ID`.
- `htrq submit --dry-run` — resolve manifest, print page count + estimated
  runtime + Job YAML without applying (D15).
- `htrq status [<ref>]` — queued (suspended) / running / succeeded / failed,
  with Kueue workload position and termination-log reasons.
- `htrq logs <ref>`, `htrq retry <ref>` — kubectl conveniences.
- `htrq report` — aggregate GPU stall fraction (`gpu_stall_seconds /
  wall_seconds`) and throughput across recent manifests: the Phase 2 evidence
  in one command.
- `htrq pipeline deploy <yaml>` — validate the pipeline (dry-run
  `Pipeline.from_config()` in the pinned image), create the immutable
  per-version ConfigMap (see [Pipeline configs](#pipeline-configs-d17)), and
  run the model warm-up Job (see [Model handling](#model-handling)) — one
  command owns "a new pipeline exists".
- `htrq pipeline list` — the deployed pipeline ids (ConfigMap names).

## Model handling

Two distinct per-Job costs — don't conflate them:

| Cost | When paid | Size |
|---|---|---|
| **download** (HF Hub → `HF_HOME`) | **once per pipeline**, by the warm-up Job — never by a batch Job | ~2–4 GB, off the GPU's clock entirely |
| **load** (`HF_HOME` → GPU) | per Job, always — `Pipeline.from_config()` instantiates step models eagerly (verified: `steps.py` builds models at construction; TrOCR `__init__` calls `from_pretrained`); every `pipeline.run(page)` reuses them | ~30–60 s, amortized to noise at volume granularity |

The streaming driver overlaps the load with the first pages' downloads (start
the downloader pool, *then* call `from_config()`), so startup GPU-idle is
`max(model_load, first_page_download)`, not the sum.

**Pre-warmed cache, read-only for Jobs (settled, D14).** Batch Jobs mount the
cache PVC `readOnly` with `HF_HUB_OFFLINE=1`: they never download and never
write, and the NetworkPolicy gives them no HF Hub egress at all. The one
writer is the **warm-up Job** (`htrflow_batch.warmup`) — same image, same
pipeline ConfigMap, CPU-only, outside the Kueue queue — which simply calls
`Pipeline.from_config()`: instantiating the pipeline *is* the download, so
exactly the files a Job will load land in the cache, with no second parser of
the pipeline YAML. Who runs it:

- **Campaigns-repo pipelines:** the reconciler, on first sight of a pipeline
  (`htr-warmup-<id>`). It submits no volumes for that pipeline until the
  warm-up's `Complete` condition lands; a failed warm-up is logged to
  `status/warmup/<id>.log`, deleted and recreated next tick, so an HF Hub
  outage heals itself. The Job is never TTL-reaped — its condition is the
  gate — so after replacing the cache PVC, delete `htr-warmup-*` to re-warm.
- **Chart-declared pipelines** (`values.pipelines`, the example Job):
  `make warmup PIPELINE=<id> IMAGE=<ref>`, which renders the same Job spec
  through `python -m htrflow_reconciler.warmup | kubectl apply`.

Alternatives kept on record: no cache (v1 — every Job re-downloads while
holding the GPU, needs `HF_TOKEN` + HF egress in every Job) and baking the
weights into the image (hermetic, but multi-GB images per pipeline). The
model-registry variant — weights as signed OCI artifacts pulled from an
in-cluster registry into the cache — is the natural next step and keeps this
mount-point contract unchanged.

Wrapper only ever sees `HF_HOME`; the cache choice is a mount-point swap.

## Pipeline configs (D17)

Pipeline YAMLs are upstream-format (any YAML the stock CLI accepts — steps,
model names, generation settings, Export steps pointing at `/work/outputs/…`).
How they travel from authoring to a result:

1. **Deploy:** one **immutable ConfigMap per pipeline version** —
   `htr-pipeline-trocr-base-hist-swe-v1` with `immutable: true`. The API server
   then *enforces* the rule that a changed pipeline is a new id: a deployed
   pipeline literally cannot be edited, only superseded by `-v2`. (Bonus:
   kubelet stops watching immutable ConfigMaps — cheaper at scale.)
2. **Select:** `htrq submit <ref> --pipeline trocr-base-hist-swe-v1` sets
   `PIPELINE_ID` (namespaces the S3 keys **and** is part of the Job-name hash,
   so the same volume under a different pipeline is a different Job, not a
   collision) and mounts that specific ConfigMap; `PIPELINE_PATH` points at the
   file inside it.
3. **Run:** wrapper calls `Pipeline.from_config($PIPELINE_PATH)` — to htrflow
   it's just a file.
4. **Provenance:** the wrapper embeds the YAML content + hash in
   `manifest.json` and uploads the YAML next to the results — every result
   stays explainable independent of cluster state.

**Deploy-time validation:** `htrq pipeline deploy <yaml>` (the same command
that runs the model warm-up above) first dry-runs `Pipeline.from_config()` in a
throwaway container of the pinned image — broken YAML or unresolvable models
fail at deploy time, not as exit-13 Jobs.

**Why not a `HtrPipeline` CRD:** the ConfigMap-per-version pattern is a
deliberate poor-man's CRD — it delivers the CR properties that matter here
(identity, API-server-enforced immutability, GitOps, kubectl UX) with zero
controllers. What it lacks vs a real CRD — admission-time schema validation,
a status subresource ("models warmed"), auto-warm-up on create — is covered
imperatively by `htrq pipeline deploy`. The maturity ladder: **v1** immutable
ConfigMaps + deploy-time validation → **v2** the API service (see
[Evolution](../roadmap/evolution.md)) lists and validates pipelines,
ConfigMaps still underneath → **v3** a real CRD only if admission-time
guarantees or a second machine consumer demand it (see
[Evolution](../roadmap/evolution.md)).
