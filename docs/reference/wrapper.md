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

A campaign is one Indexed Job, one index per volume: `$JOB_COMPLETION_INDEX`
selects a line of the campaign's `volumes.txt`. Retries, the failure policy,
the deadline and the grace period are in the rendered Job, field by field,
in [Rendered objects](rendered.md); admission is in
[Queueing](../how-it-works/queueing.md).

Before the wrapper starts, the `warmup-wait` init container waits for
`/data/warmup/<pipeline-id>.done` for up to `warmup_wait_seconds`. If the
marker never appears it prints `no warm-up marker at <path> after <n>s: the
pipeline's warm-up Job has not finished` and exits 13, which fails the index
at once ([Warm-ups fail the same way](../how-it-works/failure-handling.md#warm-ups-fail-the-same-way)).

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

**Size limits.**

- **The ConfigMap**: the API server refuses one over 1 MiB, so `render`
  splits a campaign into `-part1`, `-part2`, … at 10 000 volumes or 900 KiB
  of `volumes.txt`, whichever comes first.
- **One environment string**: `IMAGES` is one env value, and Linux caps one
  at 128 KiB (`MAX_ARG_STRLEN`). `validate` refuses an `images:` volume
  whose line is over 100 KiB. A campaign rendered before that rule could
  still fail with `Argument list too long` before the wrapper starts: split
  the volume, or give it a IIIF manifest.

**Reading the file yourself.** The ConfigMap is a normal cluster object:

```console
$ kubectl get configmap campaign-<name> -n <namespace> \
    -o jsonpath='{.data.volumes\.txt}'
R0001203	https://<iiif-host>/<path>/R0001203/manifest
loose-scans	images:https://example.org/scan1.jpg https://example.org/scan2.jpg
```

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

**Required:**

| Env var | Set by the Job from | Description |
|---------|---------------------|-------------|
| `VOLUME_REF` | the `volumes.txt` line | Volume id — last segment of the S3 result prefix |
| `IIIF_MANIFEST_URL` or `IMAGES` | the `volumes.txt` line | Exactly one of the two: a source manifest (Presentation v2 or v3, must be `http(s)`) or a whitespace-separated list of `http(s)` image URLs. `IMAGES` volumes get a synthetic Presentation 3 manifest built by the wrapper and published to `sources/<pipeline>/<volume>/manifest.json` before processing |
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
| `MAX_IMAGE_WIDTH` | `2500` | Width asked of the IIIF Image API (`/full/{w},/`). A 400 falls back to the largest size the image's `info.json` offers within the cap, and to `max` only when it offers none. Service-less canvases are fetched at native size |
| `RESUME` | `true` | Skip pages that already have **both** PAGE and ALTO in S3 and were made from the source they have now: the ALTO's `source-digest` object metadata, or for a page stored without it, its `page_source_digests` entry in the previous `manifest.json`. Every page not skipped loses its stored PAGE and ALTO before the run, after the previous `manifest.json` and then `iiif.json` are deleted; `false` therefore clears the whole volume's page files first. The run log says `[<volume>] resume: <n> done, <m> to process` |
| `LOOKAHEAD_PAGES` | `64` | Prefetch depth of the download pipeline |
| `LOOKAHEAD_BYTES` | `1073741824` | Byte bound on the same window: a page that has landed counts its size, one still downloading counts `FETCH_MAX_BYTES`, and no page is submitted past it (a page alone in the window always is) |
| `MAX_PAGES` | `0` | Truncate the volume (0 = all pages) — the knob for a fast end-to-end check of one or a handful of pages |
| `WORKDIR_PATH` | `/work` | Scratch dir (Jobs mount a 2 Gi memory-backed emptyDir) |
| `DOWNLOAD_CONCURRENCY` | `12` | Parallel page downloads |
| `MANIFEST_MAX_BYTES` | `16777216` | Byte cap on the manifest body, counted after decoding (over it: exit 13). Jobs set it from `converter.yaml`'s `manifest_max_bytes` |
| `FETCH_MAX_BYTES` | `67108864` | Byte cap on one image body (over it: the page fails without retry). Jobs set it from `converter.yaml`'s `fetch_max_bytes` |
| `DOWNLOAD_DEADLINE_SECONDS` | `300` | Wall-clock limit on one download: the manifest, or one attempt at a page. Past it the connection is cut, in the headers or the body; the manifest is then exit 1, the page is retried and, at the last attempt, deferred |
| `MAX_IMAGE_PIXELS` | `100000000` | Cap on one image's decoded size, `width × height`, read from its header after the download (over it: the page fails without retry, and the file is deleted). The byte cap above bounds the transfer, this one bounds the memory the page costs. `0` turns it off |
| `PAGE_TIMEOUT_SECONDS` | `600` | No-progress window of one page inside htrflow: the time since a step last finished or a model last finished a batch, not the page's total. Past it the page fails, like one whose worker thread died, the dead pipeline is refused every further step, and a new one is built. Threads a released pipeline cannot stop (a worker stuck in its model, the helper of a page that stopped moving, a step not shaped as expected, which is also logged at ERROR) are counted; at 8 the run ends transient so the retry gets a fresh pod |
| `IMAGE_DIGEST` | `unknown` | Provenance only — Jobs set the pipeline's digest-pinned image; recorded verbatim in `manifest.json` and in every ALTO's `htrflow-batch` Processing block |
| `HTRFLOW_BASE_REVISION` | `unknown` | Provenance only — set by the image itself (ENV next to its OCI label), stamped into every ALTO |
| `INDEX_FAILURE_COUNT` | `0` | How many times this index has failed before this pod: the Job controller's `batch.kubernetes.io/job-index-failure-count` pod annotation, through the downward API. Empty (annotation absent) reads as unset |
| `BACKOFF_LIMIT_PER_INDEX` | `-1` | The Job's `backoffLimitPerIndex`. When `INDEX_FAILURE_COUNT` has reached it this pod is the index's last attempt, and a page still deferred at verify is recorded as failed, with its reason, instead of missing. `-1` (or empty) = not told: no attempt is taken for the last |
| `LOG_SHIP_SECONDS` | `15` | How often the run's own stdout/stderr is uploaded to `status/logs/<pipeline>/<volume>.txt` while it runs (`0` = final upload only) |
| `TERMINATION_LOG_PATH` | `/dev/termination-log` | Read by `main.py`, not `Config`: where the exit reason is written |
| `HOME`, `TMPDIR`, `YOLO_CONFIG_DIR` | *(unset)* | The Job points them into the tmpfs workdir (`/work/home`, `/work/tmp`, `/work/ultralytics`) because the root filesystem is read-only, and its `sh -c` prologue creates them before exec'ing the wrapper |
| `HF_HOME`, `HF_HUB_OFFLINE` | *(unset)* | Set by the Job (`/data/hf`, `1`): models come from the read-only cache, never from Hugging Face Hub |

The per-volume wall-clock budget is not a wrapper setting: the campaign Job
renders it as the pod's `activeDeadlineSeconds` (`converter.yaml`'s
`max_seconds`, or the pipeline's own). At the deadline the kubelet SIGTERMs
the wrapper, which takes the `143` path below.

Results land at `{S3_PREFIX}/{PIPELINE_ID}/{VOLUME_REF}/…` (`Config.volume_prefix`).

**In a deployment**, a Job carries only the env its skeleton and
`converter.yaml` give it, and with `security.policies` on, job-shape refuses
one that carries more. Every setting above that neither sets —
`MAX_IMAGE_WIDTH`, `RESUME`, the lookahead bounds, `MAX_PAGES`, the download
settings, `MAX_IMAGE_PIXELS`, `PAGE_TIMEOUT_SECONDS`, `LOG_SHIP_SECONDS` —
runs at its default there. The *Set by* column of
[Configuration](configuration.md) says who can set each one.

**Hand runs.** Run by hand, outside a rendered Job, the wrapper needs the six
required vars and a credentials source; the knobs worth touching are
`MAX_PAGES` (cap it to 1 or a handful of pages, check the output, then run
the real volume with `MAX_PAGES=0`), `MAX_IMAGE_WIDTH`, `RESUME`,
`MANIFEST_MAX_BYTES`/`FETCH_MAX_BYTES` and `LOG_SHIP_SECONDS`. The compose
stack does exactly this without a cluster — see
[Try it](../getting-started/try-it.md#without-a-cluster-docker-compose).

**Workdir bound.** `LOOKAHEAD_BYTES` bounds the images in `WORKDIR_PATH`
to half the Job's memory-backed `emptyDir`: the default 1 GiB is half the
default 2 Gi, and a pipeline at a named size gets `LOOKAHEAD_BYTES` rendered
as half its size's `workdir`
([Pod sizes](campaign-yaml.md#pod-sizes)). Heavier images shorten
the lookahead window rather than overflow it
([Memory bounds](../how-it-works/wrapper.md#memory-bounds)).

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

| Code | Class | Kubernetes reaction |
|---|---|---|
| `0` | success: verify passed, `manifest.json` published | index `Complete` |
| `13` | permanent, `{"permanent": true}` | `FailIndex` at once, never retried |
| `1` | transient, `{"permanent": false}` | retried up to `backoffLimitPerIndex` (3); resume makes a retry cheap |
| `143` | SIGTERM, `{"permanent": false, "error": "SIGTERM"}` | a drain or preemption costs no retry; a deadline kill is retried like `1` |

**Exit 13** is raised for:

- a `ConfigError`: missing or invalid environment
- a manifest URL that is not http(s), or a manifest HTTP 400/401/403/404/410
- a manifest over `MANIFEST_MAX_BYTES` once decoded, or with a
  `Content-Encoding` other than `gzip`
- a manifest that is not a JSON object, has no canvases, or has a canvas
  with no image, a malformed shape or a non-http(s) image URL
- a bad pipeline: bad YAML, an unknown step or model class, a setting a
  step does not take, an `Export` step, or a pinned revision a key beside
  `model_settings` overrides (the last two before any model is built)

An exception that is also an `OSError` is never 13.

**Exit 1** is raised for:

- a manifest 5xx, 429 or other status, a network error or the download
  deadline
- the verify gate: a page **missing** (neither uploaded nor recorded as
  failed), or a run where every page processed failed and nothing was
  resumed. The message lists missing and failed pages, and the errors of
  the first 10 failed ones
- any `OSError`, including a model missing from the offline cache
  (`LocalEntryNotFoundError`): a re-warm fixes it
- `UploadOutage`, after 5 consecutive S3 upload failures
- anything else

**Exit 143** is the SIGTERM handler: termination log, final run-log ship,
`os._exit(143)`. A deadline kill also carries `status.reason:
DeadlineExceeded`, which the read API shows as `"error":
"DeadlineExceeded"`. Pages already published are never redone.

A page that fails does not by itself fail the run. Failures write one
structured reason to the termination log
(`{"stage": "stream", "permanent": false, "error": "verify failed: …"}`),
URL-redacted and clipped at 3500 characters with the counts and cause first.
What each code costs is in
[Failure Handling](../how-it-works/failure-handling.md); a dead htrflow
worker thread is under
[A dead htrflow worker thread](../how-it-works/failure-handling.md#a-dead-htrflow-worker-thread).
How one page is fetched, retried or deferred is in
[From Image to Transcription](../how-it-works/page-flow.md#the-width-capped-get).

The warm-up entrypoint uses the same codes and a
`{"stage": "warmup", "permanent", "error"}` termination message:

- **13**: a bad pipeline (as above, plus `RepositoryNotFoundError` and
  `RevisionNotFoundError`), `HF_HUB_OFFLINE` set, `PIPELINE_PATH` missing or
  unreadable, or a marker it cannot write. Its Job turns 13 into `FailJob`.
- **143**: SIGTERM, including its own 3600 s pod deadline. Retried under
  `backoffLimit: 2`.
- **1**: anything else, including a model not in the cache yet.

## Modules

Source root: [`packages/wrapper/src/htrflow_batch/`](https://github.com/AI-Riksarkivet/htrflow-batch/tree/main/packages/wrapper/src/htrflow_batch)

| Module | Description |
|--------|-------------|
| `config.py` | `Config.from_env` — the environment contract above |
| `iiif.py` | Manifest fetch with its guards (http(s) only, decoded byte cap, deadline) and the permanent/transient split; Presentation 3 and 2 parsing (`pages_from_manifest`), with one rule choosing each canvas's image for both the fetch and the viewer manifest; `redact_url`/`redact_urls` |
| `bounded.py` | What one download may cost: the client every fetch uses (at most 5 redirects, no keep-alive), the decoded byte cap (`gzip` only, inflated a chunk at a time), and the wall-clock `Deadline` that cuts a download's connections |
| `fetch.py` | One page, fetched safely (`fetch_page`): sized-image request, raster acceptance, byte cap, retry/backoff with `Retry-After`, the transient/permanent split, `stop` on abort — it never raises, a failure comes back as a `FetchResult` with an error and whether it is transient |
| `stream.py` | The streaming loop. `PageStream` — the download stream, started when it is constructed, at most `LOOKAHEAD_PAGES` submitted ahead of the consumer, results in submission (manifest) order, `close()` cancels what is queued. `consume()` (`StreamStats`, `UploadOutage`) processes each page the moment it lands, uploads, rolling-deletes |
| `progress.py` | `Progress` — writes `progress.json` after every page outcome and stage change, and republishes the interim `iiif.json` every 10 pages |
| `provenance.py` | `stamp_alto` — appends the `htrflow-batch` `<Processing>` block (image digest, htrflow base revision, wrapper version) to an ALTO after Export, before upload |
| `driver.py` | htrflow integration: build the pipeline from YAML (Export steps appended for `alto` and `page`), `process_page`, `htrflow_version` |
| `store.py` | `ResultStore` — deterministic S3 keys, explicit content types, XML parsed before upload, `page` then `alto`, `done_pages()` (both formats), bounded boto timeouts, checksums only where an operation requires one and on DeleteObjects a Content-MD5 in place of the CRC32 botocore would send (what S3-compatible stores that predate flexible checksums accept), the run-log key, `put_json_at` (bucket-root keys, e.g. `sources/`) |
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
per-page results, `page_sources`, `page_source_digests` (what a resume
compares) and `canvas_ids`,
the run metrics (`wall_seconds`, `gpu_stall_seconds`, `pages_per_second`,
`bytes_fetched`) and `viewer_url`
([field table](s3-layout.md#manifestjson-completion-marker)).

On any failure the wrapper writes the termination log before exiting
non-zero, never a completion marker. Every URL in the shipped run log, in
termination messages and in `page_sources` is redacted (no userinfo, no
query string; the boundary rules are in `iiif.redact_urls`). `source_manifest` in `manifest.json` is
written verbatim — the manifest URL the Job fetched, or, for `IMAGES`
volumes, the synthetic manifest id the wrapper published to `sources/`.

## Live run log

`status/logs/<pipeline>/<volume>.txt` (bucket root) is the run's own
stdout/stderr, uploaded every `LOG_SHIP_SECONDS` while it changes and once
more on exit, SIGTERM included, within a 90 s budget that fits the pod's
120 s grace period. The buffer keeps the first 1 MiB and the last 2 MiB.
The warm-up does not ship a log. Details are in
[Events and signals](../how-it-works/signals.md#the-run-log).
