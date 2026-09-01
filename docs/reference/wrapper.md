# Wrapper

The batch-Job container (`htrflow-batch`, module `htrflow_batch`): fetch pages
from IIIF, run the htrflow pipeline, stream per-page results to S3, publish
the viewer manifest and the completion marker. The narrative is in
[How it Works → The Wrapper](../how-it-works/wrapper.md); the failure
semantics in [Failure Handling](../how-it-works/failure-handling.md).

## Environment contract

Source: [`packages/wrapper/src/htrflow_batch/config.py`](https://github.com/carpelan/test/blob/main/packages/wrapper/src/htrflow_batch/config.py)

`Config.from_env` fails fast (exit 13) with the full list of missing required vars.

**Required:**

| Env var | Description |
|---------|-------------|
| `VOLUME_REF` | Volume id — last segment of the S3 result prefix |
| `IIIF_MANIFEST_URL` or `IMAGES` | Exactly one of the two: a source manifest (Presentation v2 or v3, must be `http(s)`) or a comma-separated list of `http(s)` image URLs. `IMAGES` volumes get a synthetic P3 manifest built by the wrapper and published to `sources/<pipeline>/<volume>/manifest.json` before processing |
| `PIPELINE_PATH` | Path to the mounted pipeline YAML (Jobs: `/config/pipeline.yaml`) |
| `PIPELINE_ID` | Pipeline id — first segment of the S3 result prefix |
| `S3_BUCKET` | Results bucket |
| `PUBLIC_RESULTS_BASE` | Browser-reachable base URL, used to build `iiif.json` ids, `viewer_url` and the `IMAGES` synthetic manifest id |

**Optional:**

| Env var | Default | Description |
|---------|---------|-------------|
| `S3_ENDPOINT` | `""` | Empty = boto3 provider default chain |
| `AWS_SHARED_CREDENTIALS_FILE` | *(boto3 default)* | Jobs set `/secrets/s3/credentials` — the mounted Secret file; credentials are never env |
| `S3_PREFIX` | `""` | Extra prefix before `<pipeline>/<volume>/` — the converter sets it from `converter.yaml`'s `legacy_layout` (`""` legacy, else `<namespace>/`) |
| `MAX_IMAGE_WIDTH` | `2500` | Downscale request sent to the IIIF Image API (`/full/{w},/`; `max` for narrower canvases; a 400 falls back to `max`). Service-less canvases are fetched at native size |
| `RESUME` | `true` | Skip pages that already have **both** PAGE and ALTO in S3 and whose `page_sources` URL is unchanged |
| `LOOKAHEAD_PAGES` | `64` | Prefetch depth of the download pipeline |
| `MAX_PAGES` | `0` | Truncate the volume (0 = all pages) — smoke tests |
| `WORKDIR_PATH` | `/work` | Scratch dir (Jobs mount a 2 Gi memory-backed emptyDir) |
| `DOWNLOAD_CONCURRENCY` | `12` | Parallel page downloads |
| `MANIFEST_MAX_BYTES` | `16777216` | Byte cap on the manifest body (over it: exit 13) |
| `FETCH_MAX_BYTES` | `67108864` | Byte cap on one image body (over it: the page fails without retry) |
| `IMAGE_DIGEST` | `unknown` | Provenance only — recorded verbatim in `manifest.json` |
| `LOG_SHIP_SECONDS` | `15` | How often the run's own stdout/stderr is uploaded to `status/logs/<pipeline>/<volume>.txt` while it runs (`0` = final upload only) |
| `MAX_SECONDS` | `0` | Per-volume wall-clock budget (`0` = none); on expiry: termination log `{"permanent": false, "error": "MAX_SECONDS"}`, final run-log ship, `os._exit(1)` — retried like any other transient failure |
| `TERMINATION_LOG_PATH` | `/dev/termination-log` | Where the exit reason is written |
| `HOME`, `TMPDIR`, `YOLO_CONFIG_DIR` | *(unset)* | Created at start when set — the Job points them into the tmpfs workdir because the root filesystem is read-only |
| `HF_HOME`, `HF_HUB_OFFLINE` | *(unset)* | Set by the Job (`/data/hf`, `1`): models come from the read-only cache, never from HF Hub |

Results land at `{S3_PREFIX}/{PIPELINE_ID}/{VOLUME_REF}/…` (`Config.volume_prefix`).

## Stages

`setup → resume → load → stream → verify → publish`; the current stage is
what the termination log reports. Details in
[The Wrapper → Stages](../how-it-works/wrapper.md#stages-around-the-streaming-loop).

## Exit codes

| Code | Class | Raised by |
|---|---|---|
| `0` | success | verify passed, `manifest.json` published |
| `13` | permanent — `{"permanent": true}` | `ConfigError` (missing env); manifest URL not http(s); manifest HTTP 400/401/403/404/410; body over `MANIFEST_MAX_BYTES`; non-JSON or non-object JSON; no canvases; a canvas without an image; bad pipeline YAML, an unknown step or model class, an `Export` step in the YAML (`ValueError` from `driver.load_pipeline`) |
| `1` | transient — `{"permanent": false}` | manifest 5xx/429/other status or a network error (`TransientManifestError`); the verify gate (pages missing or failed after fetch retries, `pipeline.run` exceptions, malformed XML); a model-load `OSError`; `UploadOutage` after 5 consecutive S3 upload failures; `MAX_SECONDS` exceeded (`{"error": "MAX_SECONDS"}`); anything else |
| `143` | SIGTERM — `{"permanent": false, "error": "SIGTERM"}` | the handler: termination log, final run-log ship, `os._exit(143)` |

Page-fetch acceptance (never a whole-run verdict on its own): 3 attempts
with 0.5 s × 2ⁿ backoff and a 120 s timeout each; textual Content-Types
(`text/*`, JSON, XML, XHTML) refused; the first chunk must carry a raster
signature (JPEG/PNG/GIF/TIFF/BMP/WebP/JP2); empty bodies refused; a body
over `FETCH_MAX_BYTES` is not retried; a partial file is always unlinked.

The warm-up entrypoint (`htrflow_batch.warmup`) uses the same codes: 13 for
`ValueError` (incl. pydantic), `yaml.YAMLError`, `KeyError` (unknown step)
and `NotImplementedError` (unknown model class), or when `HF_HUB_OFFLINE`
is set; 1 for anything else.

## Modules

Source root: [`packages/wrapper/src/htrflow_batch/`](https://github.com/carpelan/test/tree/main/packages/wrapper/src/htrflow_batch)

| Module | Description |
|--------|-------------|
| `config.py` | `Config.from_env` — the table above |
| `iiif.py` | Manifest fetch with the S5 guards and the permanent/transient split; P3 and P2 parsing (`pages_from_manifest`); `redact_url`/`redact_urls` |
| `fetch.py` | Bounded-lookahead downloader: sized-image requests, raster acceptance, byte cap, retry/backoff, `stop` on abort |
| `stream.py` | The consumer loop (`consume`, `StreamStats`, `UploadOutage`) — process a page the moment it lands, upload, rolling-delete, never further ahead than `LOOKAHEAD_PAGES` |
| `driver.py` | htrflow integration: build the pipeline from YAML (Export steps appended for `alto` and `page`), `process_page`, `htrflow_version` |
| `store.py` | `ResultStore` — deterministic S3 keys, explicit content types, XML parsed before upload, `page` then `alto`, `done_pages()` (both formats), bounded boto timeouts, the run-log key, `put_json_at` (bucket-root keys, e.g. `sources/`) |
| `synthetic.py` | `build_manifest` — the synthetic P3 manifest for `IMAGES` volumes |
| `viewer.py` | `build_viewer_manifest` — IIIF v3 manifest with ALTO annotation links (`iiif.json`) |
| `logship.py` | `LogCapture` — tees stdout/stderr, redacts URLs, ships the buffer to S3 on an interval ([Live run log](../how-it-works/live-run-log.md)) |
| `main.py` | The stage machine, the SIGTERM and `MAX_SECONDS` handlers, `IMAGES` wiring, `_changed_sources` |
| `warmup.py` | The warm-up entrypoint: `Pipeline.from_config()` fills `HF_HOME`, then drops the `<pipeline_id>.done` marker |

## Completion contract

`manifest.json` is written **last**, only after the verify gate confirms every
expected page has PAGE and ALTO in S3 — its presence is the sole "done"
signal, for anything that lists results directly. It embeds the pipeline YAML and its sha256 (the
drift ground truth), `image_digest`, per-page results, `page_sources` and
`canvas_ids` (what a resume compares), the run metrics (`wall_seconds`,
`gpu_stall_seconds`, `pages_per_second`, `bytes_fetched`) and `viewer_url`
([field table](s3-layout.md#manifestjson-completion-marker)).

On any failure the wrapper writes the termination log (local, instant)
before exiting non-zero — never a completion marker. Every URL in logs,
termination messages and `page_sources` is redacted (no userinfo, no query
string); `source_manifest` in `manifest.json` is written verbatim — the
manifest URL the Job fetched, or, for `IMAGES` volumes, the synthetic
manifest id the wrapper published to `sources/`.

## Live run log

`status/logs/<pipeline>/<volume>.txt` (bucket root, not `volume_prefix`) is
the run's own stdout/stderr, claimed at start, uploaded every
`LOG_SHIP_SECONDS` while it changed and once more on exit (including
SIGTERM), so the final object is the complete log rather than a tail.
Buffer cap 4 MiB (1 MiB head + 2 MiB tail kept, the middle dropped with a
marker). Everything else — what is and is not captured, the read API's and
the browser's side, versioned buckets — is on its own page:
[Live run log](../how-it-works/live-run-log.md).
