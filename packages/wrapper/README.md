# htrflow-batch-wrapper

The container that does the transcription work. One instance runs per volume,
as one index of a campaign's Kubernetes Indexed Job: it fetches the pages of a
IIIF manifest (or a plain list of image URLs), runs the htrflow pipeline on
each page the moment it lands, uploads PAGE and ALTO XML to S3 as it goes,
verifies that every page made it, and publishes the IIIF viewer manifest and
the `manifest.json` completion marker last. The same package carries the
warm-up entrypoint that fills the model cache for a pipeline before any batch
Job for it starts. The package imports cleanly without torch or htrflow; the
htrflow imports live inside `driver.py`'s functions.

- Narrative: [How it works → The Wrapper](../../docs/how-it-works/wrapper.md)
  and [Failure handling](../../docs/how-it-works/failure-handling.md)
- Reference: [Wrapper](../../docs/reference/wrapper.md) (full environment
  contract, exit codes, module table) and
  [S3 layout](../../docs/reference/s3-layout.md)

## Commands

Run everything from the repo root. It is a uv workspace, and a plain
`uv sync` inside this directory prunes the shared venv down to the root.

```bash
make install                                       # uv sync --all-packages
uv run --all-packages pytest -q packages/wrapper   # this package's unit tests
make test                                          # all three packages
make typecheck                                     # ty over every package
make build-wrapper                                 # the image, .docker/htrflow-batch.dockerfile
make test-driver-real                              # level-0 pin test inside the built image
```

The `htrflow` pytest marker (`test_driver_real.py`) needs the real htrflow
runtime and is skipped in the ordinary run. See
[Testing](../../docs/development/testing.md) for the four acceptance levels.

## Entrypoints

| Command | Runs as | Purpose |
|---|---|---|
| `python -m htrflow_batch` | the `wrapper` container of a campaign Job (image `ENTRYPOINT`) | Process one volume: `setup → resume → load → stream → verify → publish` |
| `python -m htrflow_batch.warmup` | the per-pipeline warm-up Job | Instantiate the pipeline once so `HF_HOME` holds every model, then drop `/data/warmup/<pipeline_id>.done` |

The Job's shell wrapper picks line `JOB_COMPLETION_INDEX + 1` of the campaign's
`volumes.txt`, exports `VOLUME_REF` and either `IIIF_MANIFEST_URL` or
`IMAGES` from it, and execs the module. Everything else comes from env the
converter rendered.

Exit codes: `0` success, `13` permanent (do not retry), `1` transient,
`143` SIGTERM. The reason is written to the termination log before exit.

## Configuration

`Config.from_env` in `config.py` is the contract. Required:

| Env var | Meaning |
|---|---|
| `VOLUME_REF` | Volume id, last segment of the S3 result prefix |
| `IIIF_MANIFEST_URL` **or** `IMAGES` | Exactly one: a Presentation v2/v3 manifest URL, or comma-separated image URLs (a synthetic manifest is built and published) |
| `PIPELINE_PATH` | Mounted pipeline YAML (`/config/pipeline.yaml` in Jobs) |
| `PIPELINE_ID` | Pipeline id, first segment of the S3 result prefix |
| `S3_BUCKET` | Results bucket |
| `PUBLIC_RESULTS_BASE` | Browser-reachable base URL for `iiif.json` ids and `viewer_url` |

Optional, with defaults: `S3_ENDPOINT` (provider chain), `S3_PREFIX` (`""`),
`MAX_IMAGE_WIDTH` (2500), `RESUME` (true), `LOOKAHEAD_PAGES` (64),
`MAX_PAGES` (0 = all), `WORKDIR_PATH` (`/work`), `DOWNLOAD_CONCURRENCY` (12),
`LOG_SHIP_SECONDS` (15), `MANIFEST_MAX_BYTES` (16 MiB), `FETCH_MAX_BYTES`
(64 MiB), `MAX_SECONDS` (0 = no budget), `IMAGE_DIGEST` (provenance only).
Results land under `{S3_PREFIX}/{PIPELINE_ID}/{VOLUME_REF}/`. The reference
page has the full table with semantics.

## Modules

| Module | Role |
|---|---|
| `config.py` | `Config.from_env`, fail-fast on missing env |
| `main.py` | The stage machine, SIGTERM and `MAX_SECONDS` handling, exit-code mapping |
| `iiif.py` | Manifest fetch with byte caps and the permanent/transient split; v2 and v3 parsing into an ordered page list |
| `synthetic.py` | Synthetic v3 manifest for `IMAGES` volumes |
| `fetch.py` | Bounded-lookahead downloader: sized requests, raster acceptance, retry and backoff |
| `stream.py` | Consumer loop: process each page as it lands, upload, rolling-delete |
| `driver.py` | htrflow integration: pipeline from YAML, `process_page`, version pin |
| `store.py` | `ResultStore`: deterministic S3 keys, content types, `done_pages()` for resume, run-log key |
| `viewer.py` | `iiif.json` with ALTO annotation links; ALTO dimension parsing |
| `logship.py` | `LogCapture`: tee stdout/stderr, redact URLs, ship to S3 on an interval |
| `warmup.py` | Warm-up entrypoint (see above) |

## Tests

`tests/` mirrors the modules one to one, with `conftest.py` providing a moto
S3 bucket and fixture manifests. Every bug found on a real cluster became a
regression test here first; see the [test log](../../docs/development/test-log.md).
