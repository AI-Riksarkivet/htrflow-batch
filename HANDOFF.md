# HANDOFF — A2 wrapper (audit remediation 2026-08-26)

Branch: `worktree-agent-a6c53b34487c4b4e7` off `feat/campaign-browser-visibility` (652a5eb).
Scope touched: `packages/wrapper/**`, `.docker/htrflow-batch.dockerfile`,
`.docker/htrflow-batch-gpu-arm64.dockerfile`. Everything below is for other packages.

## A1 reconciler / jobspec

- **New optional env** (Contracts → "Wrapper env"): `MANIFEST_MAX_BYTES` (default
  16777216) and `FETCH_MAX_BYTES` (default 67108864). The wrapper reads them from
  `Config.from_env`; jobspec only needs to pass them if the chart exposes values.
- **Exit 143 is now deliberate.** On SIGTERM the wrapper writes the termination
  message `{"stage": <setup|resume|load|stream|verify|publish>, "permanent": false,
  "error": "SIGTERM"}`, ships the final run log, and `os._exit(143)`s. Classify it as
  a retry (it is `permanent: false`); do not count it as an attempt when
  `pages_done` advanced (X5). `terminationGracePeriodSeconds` 30 (the default) is
  enough: the handler does one local write + one bounded S3 PUT.
- **New stage `load`** in termination logs (model load, between `resume` and
  `stream`). Anything that switches on stage names should accept it.
- **Warm-up exit codes (W12):** `htrflow_batch.warmup` now returns 13 for
  `ValueError` (incl. pydantic `ValidationError`), `yaml.YAMLError`, `KeyError`
  (unknown step name — htrflow `STEPS[...]`) and `NotImplementedError` (unknown
  model class — htrflow `get_model_by_name`). The warm-up cap / stop-on-13 logic
  (O4) can rely on this. Batch Jobs classify the same errors as exit 13 via
  `driver.load_pipeline` → `ValueError("bad pipeline config: ...")`.
- **`manifest.json` additions (W7):** `page_sources: {"0001": <image_url>, ...}` and
  `canvas_ids: {"0001": <source canvas id or null>, ...}`. `page_sources` URLs are
  redacted (no userinfo/query). The wrapper's resume reads the *previous*
  `manifest.json` under the volume prefix and reprocesses pages whose image URL
  changed. Nothing else in the file changed; `results`/`metrics-failed-latest.json`
  error strings are now URL-redacted too.
- **Upload order is `page/` then `alto/` (W2)** and "done" = both present. The
  reconciler's `pages_done` counts `alto/` keys (`s3.py:131`): still correct, and now
  strictly means "page complete" because ALTO lands last.
- **Manifest fetch classification (W1):** only 400/401/403/404/410, non-JSON,
  non-object JSON, non-http(s) URL and over-cap bodies are exit 13. 5xx, 429, other
  statuses and network errors are exit 1. Reconciler pre-validation (S4/S5) should
  mirror the same permanent set so the two never disagree on a volume.
- Not done here, flagged for policy: `source_manifest` in `manifest.json` is written
  verbatim (the frontend links it and status.json already publishes it). If the S6
  policy is "no query strings anywhere public", redact it in both places together.

## A3 chart / ops

- Optional values → env for `MANIFEST_MAX_BYTES` / `FETCH_MAX_BYTES` (see above).
- `podFailurePolicy`: exit 143 must not match the `FailJob on 13` rule (it does not),
  and should be treated like a disruption for attempt accounting.
- Image builds: both wrapper dockerfiles take `--build-arg HTRFLOW_BASE_REVISION=...`
  (`git -C ~/htrflow describe --tags --always --dirty` for the arm64 base; defaults to
  `v0.2.6-35f48a7` for the upstream amd64 base). It lands in the OCI label
  `se.riksarkivet.htrflow.base.revision`. The current `~/htrflow` checkout is at
  `ef0ed37b` (main). `make poc-push` / `.dagger/build.go` pass no build arg today —
  wire it in if the label should be meaningful for published images.

## B1 CI / tests / renovate

- Digest pins now exist for Renovate to track: `FROM airiksarkivet/htrflow:v0.2.6-35f48a7@sha256:e56a87f7…`
  and `COPY --from=ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1…` (both files).
- The amd64 dockerfile pins `torch==2.9.1 torchvision==0.24.1` (cu128 index, newest
  cp310 wheels there). It was **not built** here (10 GB base under qemu); the pins were
  verified against https://download.pytorch.org/whl/cu128/. The arm64 dockerfile was
  built locally (throwaway tag, removed) and reports exactly what `htrflow-batch:live-v2`
  reports: torch 2.13.0+cu130, torchvision 0.28.0+cu130, transformers 4.57.6,
  sentencepiece 0.2.2. Two observations from that build worth a look when the lock is
  next refreshed: the lock-based wrapper install downgrades `idna` 3.19→3.18 and
  `typing-inspection` 0.4.4→0.4.2 relative to the base (the workspace lock is older
  than the base's lock — `uv lock --upgrade` aligns them), and the explicit torch pin
  re-resolved one CUDA dependency (`nvidia-cusparselt-cu13`, 210 MB) to the version
  torch 2.13.0 declares.
- Wrapper deps are installed from `uv export --locked --package htrflow-batch-wrapper
  --no-dev --no-emit-project` with `--require-hashes`; a stale `uv.lock` now fails the
  image build (`--locked`), which is the intended CI signal.
- Test count: wrapper 84 → 151 (workspace 272). No htrflow needed; all fakes stay
  function-local.

## B2 docs (wrapper.md / failure-handling.md / s3-layout.md / development)

- Failure classification table: permanent (13) = config errors, 4xx/non-JSON/over-cap
  manifest, empty manifest, bad pipeline YAML, unknown step/model class; transient (1)
  = 5xx/429/network on manifest, page failures at verify, model-load OSError, S3
  outage (`UploadOutage` after 5 consecutive upload failures); 143 = SIGTERM with
  termination log.
- Stages are now `setup → resume → load → stream → verify → publish`.
- Byte caps `MANIFEST_MAX_BYTES` / `FETCH_MAX_BYTES`; redirect cap 5; http(s) only.
- Fetch acceptance: textual Content-Types refused, first chunk must carry a raster
  signature (JPEG/PNG/GIF/TIFF/BMP/WebP/JP2), empty bodies refused (all retryable),
  over-cap not retried, partial files unlinked. Service-less canvases (synthetic
  `images:` volumes) are fetched at native size — `MAX_IMAGE_WIDTH` cannot apply;
  `FETCH_MAX_BYTES` is the only bound (documented in `fetch.py`'s docstring).
- Upload order `page` then `alto`; done/verify require both; XML parsed before upload.
- `manifest.json` fields `page_sources`, `canvas_ids`; resume compares sources.
- S3 client timeouts: connect 10 s / read 60 s / 3 standard retries (log client
  unchanged: 5 s / 30 s / 2).
- Log redaction: userinfo and query strings are stripped from URLs in the run log
  (wrapper's own log handler), termination log, failure metrics and manifest.json.
- Dockerfile headers now document the pin policy and the `HTRFLOW_BASE_REVISION`
  build arg; `docs/development/test-log.md` line about "torch 2.11 cu128" is stale
  (the cu128 index has no cp310 wheel past 2.9.1).

## Deliberately not fixed (with reason)

- **apt pins** (`gcc libc6-dev python3.10-dev`): Ubuntu's archive drops superseded
  versions, so exact pins break the build on the next security update; a snapshot
  mirror is the right fix and belongs with the image/CI work.
- **arm64 base digest:** `htrflow:v0.2.6-arm64` is a local build with no registry
  digest; the revision label is the traceability substitute until it is pushed to
  Harbor.
- **Missing Content-Type on image responses** is accepted when the bytes are a raster
  image (S3/static hosts serve `application/octet-stream` or nothing); the magic-byte
  check is the real gate.
