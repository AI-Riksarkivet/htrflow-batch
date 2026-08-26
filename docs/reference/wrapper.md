# Wrapper

The batch-Job container (`htrflow-batch`, module `htrflow_batch`): fetch pages
from IIIF, run the htrflow pipeline, stream per-page results to S3, publish
the viewer manifest and the completion marker. The narrative is in
[How it Works → The Wrapper](../how-it-works/wrapper.md).

## Environment contract

Source: [`packages/wrapper/src/htrflow_batch/config.py`](https://github.com/carpelan/test/blob/main/packages/wrapper/src/htrflow_batch/config.py)

`Config.from_env` fails fast with the full list of missing required vars.

**Required:**

| Env var | Description |
|---------|-------------|
| `VOLUME_REF` | Volume id — last segment of the S3 result prefix |
| `IIIF_MANIFEST_URL` | Source manifest (Presentation v2 or v3) |
| `PIPELINE_PATH` | Path to the mounted pipeline YAML (Jobs: `/config/pipeline.yaml`) |
| `PIPELINE_ID` | Pipeline id — first segment of the S3 result prefix |
| `S3_BUCKET` | Results bucket |
| `PUBLIC_RESULTS_BASE` | Browser-reachable base URL, used to build `iiif.json` ids |

**Optional:**

| Env var | Default | Description |
|---------|---------|-------------|
| `S3_ENDPOINT` | `""` | Empty = boto3 provider default chain |
| `S3_PREFIX` | `""` | Extra prefix before `<pipeline>/<volume>/` — the reconciler pins it empty |
| `MAX_IMAGE_WIDTH` | `2500` | Downscale request sent to the IIIF Image API |
| `RESUME` | `true` | Skip pages that already have ALTO in S3 |
| `LOOKAHEAD_PAGES` | `64` | Prefetch depth of the download pipeline |
| `MAX_PAGES` | `0` | Truncate the volume (0 = all pages) — smoke tests |
| `WORKDIR_PATH` | `/work` | Scratch dir (Jobs mount a 2 Gi memory-backed emptyDir) |
| `DOWNLOAD_CONCURRENCY` | `12` | Parallel page downloads |
| `IMAGE_DIGEST` | `unknown` | Provenance only — recorded verbatim in `manifest.json` |
| `LOG_SHIP_SECONDS` | `15` | How often the run's own stdout/stderr is uploaded to `status/logs/<pipeline>/<volume>.txt` while it runs (`0` = final upload only) |

Results land at `{S3_PREFIX}/{PIPELINE_ID}/{VOLUME_REF}/…` (`Config.volume_prefix`).

## Modules

Source root: [`packages/wrapper/src/htrflow_batch/`](https://github.com/carpelan/test/tree/main/packages/wrapper/src/htrflow_batch)

| Module | Description |
|--------|-------------|
| `config.py` | `Config.from_env` — the table above |
| `iiif.py` | Manifest parsing: P3 and P2 (`pages_from_manifest`, sequences/canvases forms) |
| `fetch.py` | Sized-image download with retry, width capping |
| `stream.py` | The bounded producer/consumer pipeline (`consume`, `StreamStats`) — download ahead of the GPU, never further than `LOOKAHEAD_PAGES` |
| `driver.py` | htrflow integration: build pipeline from YAML, `process_page`, ALTO + PAGE export |
| `store.py` | `ResultStore` — deterministic S3 keys, explicit content types, `done_pages()` resume probe |
| `viewer.py` | `build_viewer_manifest` — IIIF v3 manifest with ALTO annotation links (`iiif.json`) |
| `main.py` | Stage machine (setup → fetch/transcribe → verify → publish), `publish_failure_metrics` |
| `logship.py` | `LogCapture` — tees stdout/stderr and ships the buffer to S3 on an interval, so the frontend can follow a running volume |

## Completion contract

`manifest.json` is written **last**, only after the verify gate confirms every
expected page has ALTO in S3 — it is the marker the reconciler's
`done_volumes()` probes. It embeds the pipeline YAML and its sha256 (the
drift ground truth), `image_digest`, per-page results, and the run metrics
(`wall_seconds`, `gpu_stall_seconds`, `pages_per_second`, `bytes_fetched`).

On any failure the wrapper best-effort publishes `metrics-failed-latest.json`
(stage, error, partial per-page results) before exiting non-zero — evidence
for the operator, and never a completion marker.

## Live run log

`status/logs/<pipeline>/<volume>.txt` (bucket root — the reconciler's status
namespace, not `volume_prefix`) is the run's own stdout/stderr, uploaded every
`LOG_SHIP_SECONDS` while the volume runs and once more on exit, so the final
object is the complete log rather than a tail. `kubectl logs` is unchanged
(the tee writes through). Shipping never fails a run: upload errors are
logged once and retried next interval. The buffer is capped at 4 M characters
(1 M head + 2 M tail kept, middle dropped with a marker). A retry overwrites
the key with the new attempt; the reconciler first copies the shipped log of
the failed attempt to `status/failures/…` (complete, not a kube tail).

Honest limits: only Python-level writes are teed — anything writing to fd 1/2
directly (CUDA/C++ warnings, subprocesses) shows in `kubectl logs` only; a
SIGTERM (Job deletion, preemption) exits without the final upload, so the
object then lags by up to one interval. On a **versioned** bucket each upload
is a new version (~240/h per running volume) — add a lifecycle rule or turn
shipping off with `LOG_SHIP_SECONDS=0`.
