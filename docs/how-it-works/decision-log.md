# Decision Log

| # | Decision | Status |
|---|----------|--------|
| D1 | PoC to evaluate the approach (not yet a rask replacement) | settled |
| D2 | Approach A: thin `htrflow-batch` image + plain k8s Jobs + Kueue | settled |
| D3 | Work unit: **one archival volume = one Job** | settled |
| D4 | Phase 1 input: wrapper fetches **directly from IIIF** (async, width-capped) into a tmpfs workdir; instrumented so the idle numbers decide Phase 2 | settled |
| D4b | Cache/data layer (nginx proxy vs Fluid+shim) | **deferred to Phase 2**, evidence-gated ([Phase 2: Cache Layer](../roadmap/phase-2-cache.md)) |
| D5 | GPU pod workdir: tmpfs (`emptyDir: medium: Memory`), width cap + preflight guard | settled |
| D6 | Output: **S3 (HCP)** behind a swappable `publish()` seam; keys namespaced by pipeline id | recommended, confirm |
| D7 | Submission: **`htrq` CLI**, no in-cluster components | settled |
| D8 | Wrapper must **verify outputs against inputs** after htrflow runs — htrflow's exit code is not trustworthy ([context](#known-upstream-flaw-the-design-must-absorb)) | settled |
| D16 | Wrapper drives htrflow **as a library** — streaming producer–consumer: pages process as they download, ALTOs upload as they're written. Stock-CLI modes kept as fallbacks ([The Wrapper](wrapper.md)) | settled |
| D17 | Pipelines are **immutable ConfigMaps, one per pipeline version** (`htr-pipeline-<id>`); a changed pipeline is a new id, enforced by the API server ([Pipeline configs](wrapper.md#pipeline-configs-d17)) | settled |
| D18 | **No CRD, no controller, no API service in the PoC** — `htrq` CLI only. Frontend/API ([Evolution](../roadmap/evolution.md)) and campaign-CRD ([Evolution](../roadmap/evolution.md)) are designed evolution steps, not v1 | settled |
| D19 | Viewer = **Riksarkivet universalviewer4 fork** (already renders ALTO via canvas `seeAlso`); wrapper emits a per-volume IIIF P3 manifest `iiif.json` at publish; canvas dims = width-capped processing dims; results store serves CORS + correct content-types ([output contract](wrapper.md#output-store-and-completion-contract), [Evolution](../roadmap/evolution.md)) | **validated on k3s PoC** (2026-07-28, [test log](../development/test-log.md)) — fork gotchas in [The Wrapper](wrapper.md#output-store-and-completion-contract) |
| D9–D15 | Improvement items (resume, naming, provenance, …) | open — see [Open Items](../roadmap/open-items.md) |

This table is the index into everything else in this section: each settled
decision links to the page that details it, and the two still-open rows
(D4b, D9–D15) link to where they're tracked.

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
