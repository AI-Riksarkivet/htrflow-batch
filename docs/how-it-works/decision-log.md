# Decision Log

| # | Decision | Status |
|---|----------|--------|
| D1 | PoC to evaluate the approach (not yet a rask replacement) | settled |
| D2 | Approach A: thin `htrflow-batch` image + plain k8s Jobs + Kueue | settled |
| D3 | Work unit: **one archival volume = one Job** | settled |
| D4 | Phase 1 input: wrapper fetches **directly from IIIF** (async, width-capped) into a tmpfs workdir; instrumented so the idle numbers decide Phase 2 | settled |
| D4b | Cache/data layer (nginx proxy vs Fluid+shim) | **deferred to Phase 2**, evidence-gated ([Phase 2: Cache Layer](../roadmap/phase-2-cache.md)) |
| D5 | GPU pod workdir: tmpfs (`emptyDir: medium: Memory`), width cap + preflight guard | settled |
| D6 | Output: **S3** behind a swappable store seam; keys namespaced by pipeline id | settled for S3 (the PoC runs on the devStack RustFS; a durable bucket — HCP or real S3 — is the production target, still to be confirmed) |
| D7 | Submission: `htrq` CLI, no in-cluster components | **superseded (2026-07-29)** by the GitOps reconciler: volumes are declared in a campaigns repo and a CronJob submits them ([Campaigns (GitOps)](campaigns.md)). A CLI stays a proposal for hand-run experiments ([Evolution](../roadmap/evolution.md#htrq-cli-proposal-not-built)) |
| D8 | Wrapper must **verify outputs against inputs** after htrflow runs — htrflow's exit code is not trustworthy ([context](#known-upstream-flaw-the-design-must-absorb)) | settled, built |
| D9 | Resume from partial results | **built** — per-page keys, `page/` + `alto/` both required, `page_sources` compared on resume ([The Wrapper](wrapper.md#stages-around-the-streaming-loop)) |
| D10 | Deterministic Job names as the idempotency key | **built**, later **superseded** — `htr-<pipeline>-<volume>-<8hex>`, a duplicate create was `AlreadyExists`; B63 replaced per-volume Jobs with one Indexed Job per campaign, named after the campaign file, so this exact scheme no longer applies ([Campaigns](../how-it-works/campaigns.md)) |
| D11 | Provenance manifest contents | **built** — `manifest.json` with pipeline YAML + sha, image digest, htrflow version, per-page results, timings, `page_sources` ([S3 Layout](../reference/s3-layout.md#manifestjson-completion-marker)) |
| D12 | Structured failure via termination-log | **built** — `{"stage", "permanent", "error"}` on every non-zero exit incl. SIGTERM ([Failure Handling](failure-handling.md)) |
| D13 | Priority lanes (`htr-interactive` > `htr-bulk`) | proposed, open ([Open Items](../roadmap/open-items.md)) |
| D14 | **Pod hardening**: every pod Pod-Security `restricted` (non-root, no caps, RO rootfs, seccomp, no SA token except the reconciler), secrets as files, egress-allowlist NetworkPolicies per role, a **read-only, offline model cache** written only by a per-pipeline warm-up Job the reconciler gates on ([Security](../development/security.md), [Model handling](wrapper.md#model-handling)) | **built** (2026-08-25, verified on the k3s PoC; namespace default-deny and the digest gate added 2026-08-26) |
| D15 | Submit dry-run (page count + estimated runtime before applying) | proposed, open — the reconciler's pre-validation covers the page count; runtime estimation has no home without a CLI |
| D16 | Wrapper drives htrflow **as a library** — streaming producer–consumer: pages process as they download, results upload as they're written. Stock-CLI modes kept as fallbacks ([The Wrapper](wrapper.md)) | settled, built |
| D17 | Pipelines are **immutable ConfigMaps, one per pipeline version** (`htr-pipeline-<id>`); a changed pipeline is a new id, enforced by the API server and by the reconciler's drift guards ([Pipeline configs](wrapper.md#pipeline-configs-d17)) | settled, built |
| D18 | No CRD, no controller, no API service in the PoC | **superseded in part (2026-07-29)**: still no CRD and no API service, but a reconciler **CronJob** does exist — the one in-cluster component that reads git and creates Jobs. Frontend/API and a campaign CRD remain designed evolution steps ([Evolution](../roadmap/evolution.md)) |
| D19 | Viewer = **Riksarkivet universalviewer4 fork** (already renders ALTO via canvas `seeAlso`); wrapper emits a per-volume IIIF P3 manifest `iiif.json` at publish; canvas dims = width-capped processing dims; results store serves CORS + correct content-types ([output contract](wrapper.md#output-store-and-completion-contract)) | **validated on k3s PoC** (2026-07-28, [test log](../development/test-log.md)) — fork gotchas in [The Wrapper](wrapper.md#output-store-and-completion-contract) |
| D20 | **Campaigns are GitOps**: desired state in a git repo, a reconciler CronJob derives observed state from S3 + the cluster every tick and publishes `status.json`; a read-only browser renders it. No database ([Campaigns (GitOps)](campaigns.md), [spec](../superpowers/specs/2026-07-29-campaign-gitops-design.md)) | settled (2026-07-29), built, running on the k3s PoC |

**Superseded by B63 (2026-09-01):** every row above that speaks of the GitOps
reconciler CronJob, its pre-validation, its drift guards or the `status.json`
it published (D7, D10, D13, D14, D15, D17, D18, D20) describes a component
that no longer exists — a campaign is one Indexed Job, and progress is read
live off the cluster by `packages/web` ([Campaigns (Indexed
Jobs)](campaigns.md)). The rows are left as written: they are the record of
what was decided when.

This table is the index into everything else in this section: each settled
decision links to the page that details it; the open rows (D4b, D13, D15)
link to where they're tracked.

## Rulings since B63

Decisions taken after the table above was written, each recorded the day it
was made:

| Date | Ruling |
|---|---|
| 2026-09-01 | **No CRD and no controller** (B63): a campaign is one Indexed Job rendered from git and applied; the reconciler CronJob, its pre-validation, its drift guards and the `status.json` it published are all deleted ([Campaigns](campaigns.md)) |
| 2026-09-04 | **Policy is Kyverno's, not the converter's** (Task 22): the image allow-list, the digest pin and the model-revision rule became `ClusterPolicy` objects enforced at admission — and re-run over `rendered/` by the Kyverno CLI in the campaigns repo's CI — instead of render-time checks in the converter ([Security](../development/security.md)) |
| 2026-09-07 | **Models are never baked into the image**: weights reach a GPU only through the per-pipeline warm-up Job and the read-only model-cache PVC, never through a multi-GB image per pipeline ([Model handling](wrapper.md#model-handling)) |
| 2026-09-07 | **Everyone logs in** (B67): the status page, `/api/v1/*`, the viewer, ALTO/PAGE, `iiif.json`, `manifest.json` and the run logs all go behind one login — Dex in front of GitHub and Hugging Face organisation membership, oauth2-proxy in front of the service — and the results bucket stops being anonymous-read |
| 2026-09-07 | **The stories live in the repo, not on the site**: `docs/features/` is the source of truth for the backlog and its Azure mapping, and is excluded from the published documentation site |
| 2026-09-08 | **The campaign's ConfigMap is the record, and there is no database** (B76): the Job is a window that its `ttlSecondsAfterFinished` closes, so what a campaign leaves behind is its own `campaign-<name>` ConfigMap — which has no TTL and is pruned only when the campaign file leaves git — with the provenance stamped on it, plus a small `campaign-<name>-status` ConfigMap the read API writes from what it observes. `apply` reads that record and leaves a finished campaign alone instead of re-running every volume ([The record a campaign leaves](campaigns.md#the-record-a-campaign-leaves)) |
| 2026-09-14 | **Every published image is a manifest list**: the web image is built natively on a runner of each architecture and joined under the plain tag exactly like the wrapper — a second matrix entry, never qemu or `--platform` — because a single-architecture tag is an `ImagePullBackOff` on a node of the other kind, as v0.2.0's amd64-only web image was on the arm64 PoC. What the chart pins is therefore always a manifest list's digest, never one architecture's image ([Releasing](../development/releasing.md#the-publish-workflow)) |
| 2026-09-14 | **A volume completes with its failed pages recorded**: a volume is done when every page is accounted for — uploaded, skipped by resume, or failed with a reason in `manifest.json` — so only a page *missing* from the results, or a run where every page it processed failed and nothing was resumed, fails the index ([verify](wrapper.md#stages-around-the-streaming-loop), [Failure Handling](failure-handling.md)) |
| 2026-09-14 | **A Hub token is the warm-up's alone, and it travels as env**: a private or gated model needs a credential, and `huggingface_hub` takes its token from the environment. `converter.yaml`'s `hf_token_secret` names a Secret the operator creates; the converter renders its `token` key as `HF_TOKEN` on the warm-up container only. The file route the S3 credential takes exists for the Hub too (`HF_TOKEN_PATH`), but its default sits inside `HF_HOME` — the cache PVC every campaign pod mounts — so a file would have to be mounted elsewhere and pointed at, for a pod that is short-lived, mounts no S3 Secret and holds no campaign data. Campaign pods get nothing: `HF_HUB_OFFLINE=1` and no route to the Hub ([The model cache](wrapper.md#the-model-cache)) |
| 2026-09-14 | **Two transformers lines, chosen at build time** (B102): upstream htrflow requires only `transformers>=4.47` — the `4.57.6` pin was ours — and the models do not agree on a line. A TrOCR model saved by transformers 5 (its tokenizer config carries `extra_special_tokens` as a list, plus `backend`, `is_local` and `local_files_only`, and its tokenizer is byte-level ByT5) fails to load under 4.57.6 with `'list' object has no attribute 'keys'`, which the slow-tokenizer error path reports as "requires the protobuf library" when protobuf is missing; with the config hand-edited it loads but decodes wrongly, spacing every non-ASCII letter. The base handwritten models, saved by transformers 4, fail the other way under 5.9.0 and 5.17: the positional-embedding `_float_tensor` buffer is reported UNEXPECTED and loading dies on `Cannot copy out of meta tensor`. Both verified in throwaway containers on the wrapper image. So the dockerfile takes `TRANSFORMERS_VERSION` (default `4.57.6`, the line upstream is tested on) and the Makefile, the dagger module and `publish.yml` can all pass it; a pipeline pins its image digest, so one campaigns repo can run pipelines on both lines. The pin is unconditional, and that is a change to the published image: the architecture built on the upstream base image kept that base's own `transformers` 4.48.3 and `tokenizers` 0.21.0 until now, and installs 4.57.6 like the other architecture from this release on. `make ci` never builds the wrapper image, so the publish workflow is the first proof of either build. The image ends by checking that the wrapper's own declared requirements are satisfied by what is installed — which is why the `huggingface-hub` bound was widened to `>=0.30,<2`, the newer transformers line requiring hub 1.x — and deliberately not by `uv pip check`: the arm64 base venv fails that for an unrelated reason of its own (`nvidia-cusparselt-cu13 was built for a different platform`, out of the arm64 torch pin), which blocked every arm64 build including the default line. One line again when the base models are re-saved (B102) ([Model handling](wrapper.md#model-handling)) |
| 2026-09-14 | **A pipeline is immutable while campaigns reference it, and a warm-up Job is replaced rather than refused** (B77): a Job's pod template is fixed at create, and a live apply met that twice in one day — adding `hf_token_secret` to converter.yaml made the API server answer `422 spec.template: field is immutable` for EVERY existing warm-up Job, and editing a pipeline's `image:` did the same for that pipeline's warm-up and for its running campaign's Job. Both times `apply` aborted at the first 422, so the new campaign and its warm-up were never applied at all, and the operator's answer was `kubectl delete job` by hand. Three rulings, in the order a change meets them. (1) A refusal is one object's problem: `apply` reports it in a sentence built from `details.causes` (never the message, which quotes a whole Go pod-template struct back), applies everything else, and exits **3** — a code of its own, so CI can tell "all but one object is applied and the one is named" from the total failure that stays 1. (2) A warm-up Job whose pod template changed is deleted and created again, because it is idempotent (its marker is on the cache PVC) and holds no campaign state; one that is RUNNING is left alone and reported, since the delete would take the pod that is downloading with it. A campaign Job is never replaced: its completed indexes and its results are the campaign. (3) What a campaign Job would have met as a 422 is refused earlier instead — `validate`/`render` compare each pipeline file's image and steps against `rendered/pipelines/<id>.yaml` (rendered/ is committed, so the previous render IS the record) and refuse an edit a rendered campaign still in campaigns/ would run, naming the pipeline and the campaigns. The line between (2) and (3) is deliberate: the guard compares only what the PIPELINE FILE decides, so a converter release or a converter.yaml-wide setting — which move every warm-up's pod template without changing a recipe by a word — fall to the replacement in (2) rather than blocking every render. The cluster-side finished-campaign record (B76) is not consulted: `validate` and `render` never touch a cluster, so the way to release a pipeline is the one that already retires a finished campaign — remove its file from campaigns/ ([Immutability](campaigns.md#immutability), [refused objects](../reference/campaign-yaml.md#when-the-api-server-refuses-an-object)) |
| 2026-09-14 | **RBAC says what, admission says which one** (deployment audit round): a Role grants a verb over a resource TYPE and has no way to name an object, so two grants the platform's own code needs were necessarily wider than the code — the read API's `create`/`patch` on ConfigMaps (which covered `htr-pipeline-<id>`, the pipeline a campaign Job mounts: overwrite it and the next campaign loads weights of an attacker's choosing, from the one pod browsers reach) and the apply identity's `delete` on every Job and ConfigMap in the namespace. Neither can be narrowed in RBAC, and neither is narrowed by rewriting the code, because the code was never the attacker. They are narrowed at admission instead, where the requesting ServiceAccount is visible: `templates/policies/rbac-scope.yaml`, `background: false` because a background scan has no requester. The same round: a catch-all egress stops reaching link-local and the private ranges (D3), the S3 rule names its ports (D4), the model-revision rule keys off the `pipeline.yaml` key rather than a label anyone can leave off (D5), the apply pod gets a NetworkPolicy so its identity has somewhere to go (D7), the image rules see ephemeral containers (D10), a `0.0.0.0/0` web ingress has to be accepted in so many words (D2), `values-prod.yaml` is a profile that actually enforces (D14, B80), `publish.yml`'s dispatch inputs reach the shell as data (D13), checkouts drop the token (D18), and the tests that were silently skipping in CI — the manifest validation, the commit provenance and the level-0 library-API pin — run (T3/T4/T5). Left open and deliberately: **D11** (the wrapper image's two post-export `uv pip install`s are not `--require-hashes`, unlike the exported set), **D12** (no lockfile for the base image's own inputs), **D15** (an allow-list prefix of `docker.io/riksarkivet/` admits every repository in that organisation — noted on the [Security](security.md) page, not narrowed, since narrowing it is the operator's call) and **D16** (force server-side apply — owned by the concurrent web round) ([Security](security.md), [Deploy](../getting-started/deploy.md)) |

## Context: what the htrflow image gives us

Analysis of `AI-Riksarkivet/htrflow` (v0.2.6):

- CUDA 12.1 runtime image (`docker/htrflow.dockerfile`, published to Docker Hub
  via manual workflow dispatch). Entry surface is the `htrflow` CLI — no server.
- One invocation = `htrflow pipeline <pipeline.yaml> <inputs...>` or
  `--inputs-file list.txt`. Strictly **file-in / file-out**: local image paths
  in, ALTO / PAGE XML / txt / JSON written to a local output dir. No S3/HTTP support.
- Pipeline YAML declares the steps: YOLO region segmentation → line
  segmentation → TrOCR recognition → reading order → export. Models are pulled
  from HF Hub at runtime (`HF_HOME`; private models need `HF_TOKEN`).
- Exports write **one output file per page**, and the Export step runs per
  document — ALTOs appear **incrementally during a run**, not in one dump at
  the end → per-page idempotency, resume, and streaming upload.
- `--inputs-file` lets the wrapper hand htrflow an explicit page list (resume).
- The **CLI** builds its full input list up front (every file must exist on
  disk before start) — but the underlying library loop consumes documents one
  by one. Only the CLI entry point blocks input streaming, which is what makes
  the D16 library driver possible without touching upstream.

### Known upstream flaw the design must absorb

In `cli.py`, pages are submitted to a `ThreadPoolExecutor` and **the futures
are never collected** — a page that throws inside `pipeline.run` can vanish
without failing the process. **Exit 0 does not prove all pages were
transcribed.** Consequence (D8): the wrapper verifies per-page outputs against
the input list after every run, and only publishes the completion marker when
they match. Trusting the exit code could mark incomplete volumes Complete and
silently corrupt an archive-scale backfill.

The D16 library driver additionally fixes this **at the source**: the wrapper
calls `pipeline.run(document)` itself and holds each page's result/exception
directly instead of trusting a process exit code. The verification gate stays
anyway (belt and braces — it also catches upload gaps).

**Second flaw, found on the 2026-09-08 live run (upstream Bug 3023).** In
`models/ultralytics/yolo.py`, `_simplify_polygons` puts `None` in its result
for a mask of fewer than four points ("to use the bounding box instead"),
but the `map(Polygon, …)` consumed at line 87 then raises
`TypeError: 'NoneType' object is not iterable` in `utils/geometry.py:205`.
It is raised inside `Inference._process`'s daemon thread, which dies
silently, so `pipeline.run()` never returns: the wrapper stalls with the GPU
reserved until the pod's `activeDeadlineSeconds`, emitting no signal at all.
Seen on volume R0001203: the last export was `0043.xml`, and page 0044 never
came back. The wrapper's answer is **B88** —
bound the wait on a page so a dead model thread fails that page instead of
the run; the fix itself belongs upstream.
