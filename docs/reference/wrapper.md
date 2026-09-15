# Wrapper

The batch-Job container (`htrflow-batch`, module `htrflow_batch`): fetch pages
from IIIF, run the htrflow pipeline, stream per-page results to S3, publish
the viewer manifest and the completion marker. This page is its whole
contract — the Job it runs in, where its volume comes from, its environment,
its exit codes and what it leaves behind. The narrative is in
[How it Works → The Wrapper](../how-it-works/wrapper.md); the failure
semantics in [Failure Handling](../how-it-works/failure-handling.md). To run
the wrapper without a cluster, see [Try it](../getting-started/try-it.md).

## The Job contract

A whole **campaign** is one Kubernetes `batch/v1` Job with
`completionMode: Indexed` — one index per volume, `$JOB_COMPLETION_INDEX`
selecting a line of the campaign's `volumes.txt`. It carries the Kueue queue
label, and Kueue's webhook suspends it on creation and unsuspends it (up to
`parallelism`) as quota frees — the rendered file itself has
`suspend: false` unless the campaign declares `suspend: true`:

```yaml
kind: Job
metadata:
  labels:
    kueue.x-k8s.io/queue-name: htr-batch
spec:
  completionMode: Indexed
  completions: 200                  # = number of volumes in the campaign
  parallelism: 20                   # the campaign's window, clamped to converter.yaml's
  backoffLimitPerIndex: 3
  maxFailedIndexes: 200             # = completions: one bad volume never stops the rest
  podFailurePolicy:
    rules:
      - action: Ignore              # a preempted or drained pod is not this index's failure
        onPodConditions: [{ type: DisruptionTarget }]
      - action: FailIndex           # the wrapper's permanent failure
        onExitCodes: { containerName: wrapper, operator: In, values: [13] }
      - action: FailIndex           # the warm-up gate gave up on its marker
        onExitCodes: { containerName: warmup-wait, operator: In, values: [13] }
  ttlSecondsAfterFinished: 604800  # a week: converter.yaml's default, else the pipeline's
  template:
    spec:
      activeDeadlineSeconds: 21600  # the per-volume budget: pipeline max_seconds, else converter.yaml's
      terminationGracePeriodSeconds: 120
```

Submit a 200-volume campaign and exactly `parallelism` indexes run at once;
the rest wait in index order within the campaign (`kubectl get workloads -n
<namespace>`). Admission, priorities and preemption are in
[Queueing](../how-it-works/queueing.md). The complete spec the converter
renders — resources, mounts, labels, hardening — is in
[The Wrapper](../how-it-works/wrapper.md) and, verbatim, in the skeleton
[`manifests/campaign-job.yaml`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/packages/converter/src/htrflow_converter/manifests/campaign-job.yaml).

Before the wrapper starts, the `warmup-wait` init container waits for its
pipeline's warm-up marker, `/data/warmup/<pipeline-id>.done`, for up to
`converter.yaml`'s `warmup_wait_seconds`. If the marker never appears it
prints `no warm-up marker at <path> after <n>s: the pipeline's warm-up Job
has not finished` and exits 13, which fails the index at once: a retry would
hold the GPU for another wait on a marker that is not coming.

## Where a volume comes from: `volumes.txt`

Every volume in a campaign becomes exactly one line of a `ConfigMap` —
`campaign-<name>`'s `volumes.txt` — written by the converter from the
campaign file's `volumes:` list, in file order. A two-volume render (one
bare reference, one `images:` volume) produces this `volumes.txt`:

```title="volumes.txt"
R0001203	https://<iiif-host>/<path>/R0001203/manifest
loose-scans	images:https://example.org/scan1.jpg https://example.org/scan2.jpg
```

**Line format** (`Volume.source_line()` in
[`models.py`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/packages/converter/src/htrflow_converter/models.py)):
`<id>` and the source, separated by the **first tab** — the shell prologue
below splits on it. The source half is one of two shapes:

- a plain IIIF manifest URL — `<id>\t<manifest-url>` — for a bare reference
  (expanded through `converter.yaml`'s `source_template`) or an explicit
  `manifest:` volume;
- `images:` followed by every URL under that volume's `images:` list,
  space-joined — `<id>\timages:<url1> <url2> …` — for an `images:` volume.
  Whitespace is the separator because a URL can never contain it, while a
  comma is legal anywhere in one (a IIIF size such as `/full/2500,/`), so
  `validate` refuses a URL with whitespace in it rather than encoding it.
  There is no manifest at all for these; the wrapper builds and publishes a
  synthetic one itself.

**How index *i* reads line *i*.** The campaign Job's container command
(`manifests/campaign-job.yaml`) does the whole job in a few shell lines before
`exec`ing the wrapper:

```sh
set -eu
mkdir -p "$HOME" "$TMPDIR" "$YOLO_CONFIG_DIR"
line=$(sed -n "$((JOB_COMPLETION_INDEX + 1))p" /campaign/volumes.txt)
[ -n "$line" ] || { echo "no volume for index $JOB_COMPLETION_INDEX" >&2; exit 13; }
id=${line%%	*}; src=${line#*	}
export VOLUME_REF="$id"
case "$src" in images:*) export IMAGES="${src#images:}" ;; *) export IIIF_MANIFEST_URL="$src" ;; esac
exec python -m htrflow_batch
```

`sed` is 1-indexed, `$JOB_COMPLETION_INDEX` is 0-indexed, so index 0 reads
line 1 (`+ 1`), and so on — index 0 above gets `VOLUME_REF=R0001203` and
`IIIF_MANIFEST_URL=https://<iiif-host>/<path>/R0001203/manifest`; index 1
gets `VOLUME_REF=loose-scans` and
`IMAGES=https://example.org/scan1.jpg https://example.org/scan2.jpg` — the
URLs are separated by a **space**, the one character a URL cannot carry
unescaped. An
index past the end of the file (`completions` is set from the same volume
list, so it should not occur) gets an empty `line` and exits 13, `FailIndex`,
rather than running the wrapper with nothing to work on.

**Size limits.** Two different caps bite two different ways:

- **The ConfigMap itself**: the API server refuses any ConfigMap over
  1 MiB, and an `images:` volume's entire URL list is one line — 300 pages
  at 74 characters per URL is already 22.5 kB on that single line, so 47
  such volumes exceed 1 MiB on their own. `render` splits a campaign into
  `-part1`, `-part2`, … before that happens: by volume count (10 000) and by
  accumulated `volumes.txt` bytes (900 KiB per part — margin under the 1 MiB
  hard limit, not the limit itself), whichever comes first. Each part is its
  own Job and ConfigMap.
- **A single environment string**: `IMAGES` is exported as one environment
  value, and Linux caps any single argument or environment string at
  128 KiB (`MAX_ARG_STRLEN`, inside the `ARG_MAX` budget). 2 000 URLs at 100 characters each is already
  200 kB — over the limit on one volume alone, regardless of the ConfigMap
  byte budget above. `validate` does not check this: the `exec` fails with
  `Argument list too long` before the wrapper starts. Split an oversized
  `images:` volume into several smaller ones, or give it a real IIIF
  manifest instead.

**Reading the file yourself.** The ConfigMap is a normal cluster object:

```console
$ kubectl get configmap campaign-<name> -n <namespace> \
    -o jsonpath='{.data.volumes\.txt}'
R0001203	https://<iiif-host>/<path>/R0001203/manifest
loose-scans	images:https://example.org/scan1.jpg https://example.org/scan2.jpg
```

(the `\.` escapes the literal dot in the key name `volumes.txt`, which
`jsonpath` would otherwise read as a nested field lookup).

## Environment contract

Source: [`packages/wrapper/src/htrflow_batch/config.py`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/packages/wrapper/src/htrflow_batch/config.py)

`Config.from_env` fails fast (exit 13) with the full list of missing required
vars, and when both or neither of `IIIF_MANIFEST_URL` and `IMAGES` are set.
`Config` is a frozen pydantic model whose fields carry their own env name
(`Field(alias=…)`) — there is no second table of names to keep in step, and
the class-level default is the only default. A value pydantic cannot coerce
(`MAX_PAGES=abc`) is also exit 13. The generated
[Configuration](configuration.md) page lists these keys next to the other
three surfaces'.

**No wrapper setting is ever a secret.** S3 credentials reach the pod as a
mounted Secret file (`AWS_SHARED_CREDENTIALS_FILE=/secrets/s3/credentials`),
never as an env var: an env var is readable in `kubectl describe`, in a crash
dump and in every child process. `test_config.py` fails if any name below ever
matches `KEY|TOKEN|PASSWORD|SECRET_ACCESS`.

**Why these names are bare** and the web front's are `HTRFLOW_`-prefixed: this
is an in-pod contract, written by the Job the converter renders into a pod
environment nothing else writes, and renaming one would break every campaign
Job in flight. The web front is a long-lived service an operator configures,
so its settings are namespaced.

**Required:**

| Env var | Set by the Job from | Description |
|---------|---------------------|-------------|
| `VOLUME_REF` | the `volumes.txt` line | Volume id — last segment of the S3 result prefix |
| `IIIF_MANIFEST_URL` or `IMAGES` | the `volumes.txt` line | Exactly one of the two: a source manifest (Presentation v2 or v3, must be `http(s)`) or a whitespace-separated list of `http(s)` image URLs (a list joined with commas by a render older than the whitespace rule is still read, as long as every piece is itself an `http(s)` URL). `IMAGES` volumes get a synthetic Presentation 3 manifest built by the wrapper and published to `sources/<pipeline>/<volume>/manifest.json` before processing |
| `PIPELINE_PATH` | fixed, `/config/pipeline.yaml` | Path to the mounted pipeline YAML |
| `PIPELINE_ID` | the pipeline id | First segment of the S3 result prefix after `S3_PREFIX` |
| `S3_BUCKET` | the S3 Secret's `S3_BUCKET` key | Results bucket |
| `PUBLIC_RESULTS_BASE` | `converter.yaml`'s `public_results_base` | Browser-reachable base URL, used to build `iiif.json` ids, `viewer_url` and the `IMAGES` synthetic manifest id |

**Optional:**

| Env var | Default | Description |
|---------|---------|-------------|
| `S3_ENDPOINT` | `""` | From the S3 Secret's optional `S3_ENDPOINT` key. Empty = the boto3 provider default chain |
| `AWS_SHARED_CREDENTIALS_FILE` | *(boto3 default)* | Read by boto3, not `Config`. Jobs set `/secrets/s3/credentials` — the mounted Secret file; credentials are never env |
| `S3_PREFIX` | `""` | Extra prefix before `<pipeline>/<volume>/` (and before `sources/`); leading and trailing `/` are stripped. The converter always sets it to `<namespace>/`; empty only when the wrapper is run by hand |
| `MAX_IMAGE_WIDTH` | `2500` | Downscale request sent to the IIIF Image API (`/full/{w},/`; `max` for narrower canvases; a 400 falls back to `max`). Service-less canvases are fetched at native size |
| `RESUME` | `true` | Skip pages that already have **both** PAGE and ALTO in S3 and whose `page_sources` URL is unchanged. The run log says `[<volume>] resume: <n> done, <m> to process` |
| `LOOKAHEAD_PAGES` | `64` | Prefetch depth of the download pipeline |
| `MAX_PAGES` | `0` | Truncate the volume (0 = all pages) — the knob for a fast end-to-end check of one or a handful of pages |
| `WORKDIR_PATH` | `/work` | Scratch dir (Jobs mount a 2 Gi memory-backed emptyDir) |
| `DOWNLOAD_CONCURRENCY` | `12` | Parallel page downloads |
| `MANIFEST_MAX_BYTES` | `16777216` | Byte cap on the manifest body (over it: exit 13). Jobs set it from `converter.yaml`'s `manifest_max_bytes` |
| `FETCH_MAX_BYTES` | `67108864` | Byte cap on one image body (over it: the page fails without retry). Jobs set it from `converter.yaml`'s `fetch_max_bytes` |
| `IMAGE_DIGEST` | `unknown` | Provenance only — Jobs set the pipeline's digest-pinned image; recorded verbatim in `manifest.json` and in every ALTO's `htrflow-batch` Processing block |
| `HTRFLOW_BASE_REVISION` | `unknown` | Provenance only — set by the image itself (ENV next to its OCI label), stamped into every ALTO |
| `LOG_SHIP_SECONDS` | `15` | How often the run's own stdout/stderr is uploaded to `status/logs/<pipeline>/<volume>.txt` while it runs (`0` = final upload only) |
| `TERMINATION_LOG_PATH` | `/dev/termination-log` | Read by `main.py`, not `Config`: where the exit reason is written |
| `HOME`, `TMPDIR`, `YOLO_CONFIG_DIR` | *(unset)* | The Job points them into the tmpfs workdir (`/work/home`, `/work/tmp`, `/work/ultralytics`) because the root filesystem is read-only, and its `sh -c` prologue creates them before exec'ing the wrapper |
| `HF_HOME`, `HF_HUB_OFFLINE` | *(unset)* | Set by the Job (`/data/hf`, `1`): models come from the read-only cache, never from Hugging Face Hub |

The per-volume wall-clock budget is not a wrapper setting: the campaign Job
renders it as the pod's `activeDeadlineSeconds` (`converter.yaml`'s
`max_seconds`, or the pipeline's own). At the deadline the kubelet SIGTERMs
the wrapper, which takes the `143` path below.

Results land at `{S3_PREFIX}/{PIPELINE_ID}/{VOLUME_REF}/…` (`Config.volume_prefix`).

**Hand runs.** Run by hand, outside a rendered Job, the wrapper needs the six
required vars and a credentials source; the knobs worth touching are
`MAX_PAGES` (cap it to 1 or a handful of pages, check the output, then run
the real volume with `MAX_PAGES=0`), `MAX_IMAGE_WIDTH`, `RESUME`,
`MANIFEST_MAX_BYTES`/`FETCH_MAX_BYTES` and `LOG_SHIP_SECONDS`. The compose
stack does exactly this without a cluster — see
[Try it](../getting-started/try-it.md#without-a-cluster-docker-compose).

**Workdir bound.** The images in flight are what sits in `WORKDIR_PATH`:
`LOOKAHEAD_PAGES` × `FETCH_MAX_BYTES` — 64 × 64 MiB = 4 GiB worst case
against the Job's 2 Gi memory-backed `emptyDir`, which the kubelet answers
with eviction rather than a clean failure. Sized IIIF requests
(`MAX_IMAGE_WIDTH`) land at ~1 MB a page, so the bound only bites volumes of
service-less canvases fetched at native size (see `fetch.py`'s "Known limit").
Pre-size such image lists, or lower `LOOKAHEAD_PAGES`/`FETCH_MAX_BYTES` for
them.

### Warm-up entrypoint

`python -m htrflow_batch.warmup` runs in a pipeline's warm-up Job
(`htr-warmup-<id>`): it builds the pipeline once, which downloads its models
into `HF_HOME`, then drops the marker the batch pods' `warmup-wait` gate looks
for. It does not use `Config`; it reads:

| Env var | Job sets | Meaning |
|---------|----------|---------|
| `PIPELINE_PATH` | `/config/pipeline.yaml` | The pipeline to build; missing or not a file is exit 13 |
| `PIPELINE_ID` | the pipeline id | Names the marker, `<HF_HOME's parent>/warmup/<PIPELINE_ID>.done` |
| `HF_HOME` | `/data/hf` | The model cache on the PVC, the only writer of it |
| `HF_HUB_OFFLINE` | *(unset)* | Must be unset, empty, `0` or `false`: an offline warm-up downloads nothing, so it exits 13 rather than open the gate on an empty cache |
| `HF_TOKEN` | from `converter.yaml`'s `hf_token_secret`, when one is named | A Hugging Face token, for a private or gated model. The warm-up reads its **presence** and nothing else — it logs one line saying a token is set, and leaves the value to `huggingface_hub`. Campaign pods never get one |
| `TERMINATION_LOG_PATH` | *(unset)* | As for the wrapper |

The warm-up Job also sets `CUDA_VISIBLE_DEVICES=""` and the same
`WORKDIR_PATH`/`HOME`/`TMPDIR`/`YOLO_CONFIG_DIR` workdir paths as a batch
pod.

## Stages

`config → setup → resume → load → stream → verify → publish`; the current
stage is what the termination log reports.

`progress.json` records the same stages **except `config`**, and adds two of
its own: `done` after publish, and `failed` on any exit that is not a
success. The file starts at `setup`, because the tracker that writes it is
built only once the config stage has passed — so a `ConfigError` (exit 13: a
missing or invalid environment) leaves **no `progress.json` at all**, and the
termination message is the only evidence. Details in
[The Wrapper](../how-it-works/wrapper.md).

## Exit codes

| Code | Class | Raised by | Kubernetes reaction |
|---|---|---|---|
| `0` | success | verify passed, `manifest.json` published | index `Complete`; `manifest.json` in S3 = done |
| `13` | permanent — `{"permanent": true}` | `ConfigError` (missing or invalid env); manifest URL not http(s); manifest HTTP 400/401/403/404/410; body over `MANIFEST_MAX_BYTES`; non-JSON or non-object JSON; no canvases; a canvas without an image, with a malformed shape, or with a non-http(s) image URL; bad pipeline YAML, an unknown step or model class, an `Export` step in the YAML (`ValueError` from `driver.load_pipeline`). An exception that is *also* an `OSError` never lands here — see the `1` row | `podFailurePolicy` fails the index at once (`FailIndex`) — never retried |
| `1` | transient — `{"permanent": false}` | manifest 5xx/429/other status or a network error (`TransientManifestError`); the verify gate, for a page **missing** (neither uploaded nor recorded as failed) or for a run where every page it processed failed and nothing was resumed — the message lists the missing and failed page names and, for the first 10 failed pages, the error behind each (clipped to 200 chars); every page failure is also logged as it happens, and a page that failed does not by itself fail the run; any `OSError`, including one that is also a `ValueError` — `huggingface_hub.errors.LocalEntryNotFoundError` (a model missing from the read-only `HF_HOME` cache under `HF_HUB_OFFLINE=1`) is an `OSError` on every version of the library and a `ValueError` on the older line too, and a re-warm fixes it; `UploadOutage` after 5 consecutive S3 upload failures; anything else | retried up to `backoffLimitPerIndex` (3); resume makes a retry cheap |
| `143` | SIGTERM — `{"permanent": false, "error": "SIGTERM"}` | the handler: termination log, final run-log ship, `os._exit(143)`. Sent by a node drain, a preemption, or by the kubelet when the pod's `activeDeadlineSeconds` expires — the pod then also carries `status.reason: DeadlineExceeded`, which the read API surfaces as `"error": "DeadlineExceeded"` | a drain or preemption carries `DisruptionTarget`, so the attempt is not counted against `backoffLimitPerIndex` and the index runs again; a deadline kill is counted and retried like exit 1 — either way, pages already published are not redone |

Failures write one structured reason to the termination log
(`{"stage": "stream", "permanent": false, "error": "verify failed: N missing, M failed errors: … missing=[…]"}`)
so `kubectl describe pod` and the campaign page show *why* without reading
the log. Every URL in `error` is redacted, and `error` is clipped at 3500
characters with `...(truncated)` — the counts and the cause come first, the
page-name lists last, so what is dropped is the names. The full matrix is in
[Failure Handling](../how-it-works/failure-handling.md).

Page-fetch acceptance (never a whole-run verdict on its own): 3 attempts
with 0.5 s × 2ⁿ backoff and a 120 s timeout each; textual Content-Types
(`text/*`, JSON, XML, XHTML) refused; the first chunk must carry a raster
signature (JPEG/PNG/GIF/TIFF/BMP/WebP/JP2); empty bodies refused; a body
over `FETCH_MAX_BYTES` is not retried; a partial file is always unlinked.

The warm-up entrypoint uses the same codes and writes the same
`{"stage": "warmup", "permanent", "error"}` termination message:

- **13** for `ValueError` (incl. pydantic), `yaml.YAMLError`, `KeyError`
  (unknown step), `NotImplementedError` (unknown model class),
  `RepositoryNotFoundError` and `RevisionNotFoundError` (a bad model id or
  revision); when `HF_HUB_OFFLINE` is set; when `PIPELINE_PATH` is missing or
  unreadable; and when the `<pipeline_id>.done` marker cannot be written (the
  message names the file). The warm-up Job's `podFailurePolicy` turns 13 into
  `FailJob`.
- **143** for SIGTERM — a node drain, a preemption, or the pod's own
  `activeDeadlineSeconds` (3600 s, on the pod template rather than the Job,
  so the kubelet kills the pod and its message stays readable; a Job-level
  deadline would delete the pod and the message with it). A deadline kill
  is counted and retried under the Job's `backoffLimit: 2`.
- **1** for anything else, including `LocalEntryNotFoundError` — a model not
  in the cache yet is a network miss, not a bad pipeline, even on the library
  line where it also subclasses `ValueError`.

## Modules

Source root: [`packages/wrapper/src/htrflow_batch/`](https://github.com/AI-Riksarkivet/htrflow-batch/tree/main/packages/wrapper/src/htrflow_batch)

| Module | Description |
|--------|-------------|
| `config.py` | `Config.from_env` — the environment contract above |
| `iiif.py` | Manifest fetch with its guards (http(s) only, byte cap; `main.py`'s client caps redirects at 5) and the permanent/transient split; Presentation 3 and 2 parsing (`pages_from_manifest`); `redact_url`/`redact_urls` |
| `fetch.py` | One page, fetched safely (`fetch_page`): sized-image request, raster acceptance, byte cap, retry/backoff, `stop` on abort — it never raises, a failure comes back as a `FetchResult` with an error |
| `stream.py` | The streaming loop. `PageStream` — the download stream, started when it is constructed, at most `LOOKAHEAD_PAGES` submitted ahead of the consumer, results in submission (manifest) order, `close()` cancels what is queued. `consume()` (`StreamStats`, `UploadOutage`) processes each page the moment it lands, uploads, rolling-deletes |
| `progress.py` | `Progress` — writes `progress.json` after every page outcome and stage change, and republishes the interim `iiif.json` every 10 pages |
| `provenance.py` | `stamp_alto` — appends the `htrflow-batch` `<Processing>` block (image digest, htrflow base revision, wrapper version) to an ALTO after Export, before upload |
| `driver.py` | htrflow integration: build the pipeline from YAML (Export steps appended for `alto` and `page`), `process_page`, `htrflow_version` |
| `store.py` | `ResultStore` — deterministic S3 keys, explicit content types, XML parsed before upload, `page` then `alto`, `done_pages()` (both formats), bounded boto timeouts, the run-log key, `put_json_at` (bucket-root keys, e.g. `sources/`) |
| `synthetic.py` | `build_manifest` — the synthetic Presentation 3 manifest for `IMAGES` volumes |
| `viewer.py` | `build_viewer_manifest` — IIIF Presentation 3 manifest with ALTO annotation links (`iiif.json`) |
| `logship.py` | `LogCapture` — tees stdout/stderr, redacts every URL appended to the buffer (`_append`) and every URL in the wrapper's own log records (`RedactingFormatter`), ships the buffer to S3 on an interval ([Events and signals](../how-it-works/signals.md)) |
| `publish.py` | The publish stage: `alto_dims` (viewer dimensions from the ALTO), `run_manifest` (the `manifest.json` body), `run` (`iiif.json`, `pipeline.yaml`, `manifest.json` last) |
| `main.py` | The stage machine (`_setup`/`_resume`/`_stream`/`_verify`, then `publish.run`), the SIGTERM handler, `IMAGES` wiring, `_changed_sources` |
| `warmup.py` | The warm-up entrypoint: building the pipeline fills `HF_HOME`, then it drops the `<pipeline_id>.done` marker — before the success log, and a failure to write it exits 13. Same SIGTERM handling as `main.py` |

## Completion contract

`manifest.json` is written **last**, only after the verify gate confirms every
page is accounted for — PAGE and ALTO in S3, skipped by resume, or recorded
as failed with a reason — and its presence is the sole "done" signal for
anything that lists results directly. A volume can therefore be done *and*
have lost pages; `pages_ok` and `pages_failed` say which it was. It embeds
the pipeline YAML and its sha256 (the drift ground truth), `image_digest`,
per-page results, `page_sources` and `canvas_ids` (what a resume compares),
the run metrics (`wall_seconds`, `gpu_stall_seconds`, `pages_per_second`,
`bytes_fetched`) and `viewer_url`
([field table](s3-layout.md#manifestjson-completion-marker)).

On any failure the wrapper writes the termination log (local, instant)
before exiting non-zero — never a completion marker. Every URL in the
shipped run log, in termination messages and in `page_sources` is redacted
(no userinfo, no query string); `source_manifest` in `manifest.json` is
written verbatim — the manifest URL the Job fetched, or, for `IMAGES`
volumes, the synthetic manifest id the wrapper published to `sources/`.

## Live run log

`status/logs/<pipeline>/<volume>.txt` (bucket root, not `volume_prefix`) is
the run's own stdout/stderr, claimed at start, uploaded every
`LOG_SHIP_SECONDS` while it changed and once more on exit (including
SIGTERM), so the final object is the complete log rather than a tail.
Buffer cap 4 MiB (1 MiB head + 2 MiB tail kept, the middle dropped with a
marker). On SIGTERM that final ship is bounded but not instant — the
shipping-thread join (30 s), then waiting out a periodic PUT that still
holds `_upload_lock`, then one PUT of its own, each through the log client
(5 s connect, 30 s read, 2 attempts) — so the Job template gives the pod
`terminationGracePeriodSeconds: 120` rather than the default 30 s. That
covers the common case with room to spare, not the ≈ 140 s worst case, which
needs an S3 endpoint that answers nothing and where the final PUT would fail
anyway (see [Failure handling](../how-it-works/failure-handling.md)). The
warm-up Job does not capture or ship its log (it mounts no S3 Secret) and
keeps the default grace period. What is and is not captured, the read API's
and the browser's side, and versioned buckets are in
[Events and signals](../how-it-works/signals.md).
