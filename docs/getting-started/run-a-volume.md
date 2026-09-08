# Run a Volume

The normal way to run a volume is to declare it in the campaigns repo and let
the converter's rendered Job pick it up ([Running a Campaign](campaigns.md)).
This page is about what one volume's **index** *is* — for reading what the
converter renders, and for hand-run experiments with the wrapper directly
(no cluster needed — see [Local compose alternative](#local-compose-alternative)).

## The Job contract

A whole **campaign** is one Kubernetes `batch/v1` Job with
`completionMode: Indexed` — one index per volume, `$JOB_COMPLETION_INDEX`
selecting a line of the campaign's `volumes.txt`. It carries the Kueue queue
label, and Kueue's webhook suspends it on creation and unsuspends it (up to
`parallelism`) as quota frees — the rendered file itself has no `suspend`
field unless the campaign declares `suspend: true`:

```yaml
kind: Job
metadata:
  labels:
    kueue.x-k8s.io/queue-name: htr-batch
spec:
  completionMode: Indexed
  completions: 200                  # = number of volumes in the campaign
  parallelism: 20                   # converter.yaml's window, or the campaign's own
  backoffLimitPerIndex: 3
  maxFailedIndexes: 200
  podFailurePolicy:
    rules:
      - action: Ignore
        onPodConditions: [{ type: DisruptionTarget }]
      - action: FailIndex
        onExitCodes: { containerName: wrapper, operator: In, values: [13] }
  ttlSecondsAfterFinished: 86400
```

Submit a 200-volume campaign and exactly `parallelism` indexes run at once;
the rest wait in FIFO order within the campaign (`kubectl get workloads -n
htr-batch`). No preemption, no cohorts in Phase 1. The complete spec the
converter renders — resources, mounts, labels, hardening — is in
[The Wrapper → Job template](../how-it-works/wrapper.md#job-template-one-campaign-one-indexed-job).

## Where a volume comes from: volumes.txt

Every volume in a campaign becomes exactly one line of a `ConfigMap` —
`campaign-<name>`'s `volumes.txt` — written by the converter from the
campaign file's `volumes:` list, in file order. A two-volume render (one
bare Riksarkivet reference, one `images:` volume) produces this real
`volumes.txt` (from
[the worked example](../how-it-works/rendering-example.md#the-campaign-configmap)):

```title="volumes.txt"
R0001203	https://lbiiif.riksarkivet.se/arkis!R0001203/manifest
loose-scans	images:https://example.org/scan1.jpg,https://example.org/scan2.jpg
```

**Line format** (`Volume.source_line()` in
[`models.py`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/packages/converter/src/htrflow_converter/models.py)):
`<id>` and the source, separated by a **tab** — the shell prologue below
splits on it, so a plain space in a query string or an id would be
ambiguous with the field separator itself. The source half is one of two
shapes:

- a plain IIIF manifest URL — `<id>\t<manifest-url>` — for a bare reference
  (expanded through `converter.yaml`'s `source_template`) or an explicit
  `manifest:` volume;
- `images:` followed by every URL under that volume's `images:` list,
  comma-joined with no spaces — `<id>\timages:<url1>,<url2>,…` — for an
  `images:` volume. There is no manifest at all for these; the wrapper
  builds and publishes a synthetic one itself.

**How index *i* reads line *i*.** The campaign Job's container command
(`manifests/campaign-job.yaml`) does the whole job in one shell line before
`exec`ing the wrapper:

```sh
line=$(sed -n "$((JOB_COMPLETION_INDEX + 1))p" /campaign/volumes.txt)
[ -n "$line" ] || { echo "no volume for index $JOB_COMPLETION_INDEX" >&2; exit 13; }
id=${line%%	*}; src=${line#*	}
export VOLUME_REF="$id"
case "$src" in images:*) export IMAGES="${src#images:}" ;; *) export IIIF_MANIFEST_URL="$src" ;; esac
```

`sed` is 1-indexed, `$JOB_COMPLETION_INDEX` is 0-indexed, so index 0 reads
line 1 (`+ 1`), and so on — index 0 above gets `VOLUME_REF=R0001203` and
`IIIF_MANIFEST_URL=https://lbiiif.riksarkivet.se/arkis!R0001203/manifest`;
index 1 gets `VOLUME_REF=loose-scans` and
`IMAGES=https://example.org/scan1.jpg,https://example.org/scan2.jpg`. An
index past the end of the file (should never happen — `completions` is set
from the same volume list) gets an empty `line` and exits 13, `FailIndex`,
rather than running the wrapper with nothing to work on.

**Size limits.** Two different caps bite two different ways:

- **The ConfigMap itself** (B72): the API server refuses any ConfigMap over
  1 MiB, and an `images:` volume's entire URL list is one line — 300 pages
  at 74 characters per URL is already 22.5 kB on that single line, so 47
  such volumes exceed 1 MiB on their own. `render` splits a campaign into
  `-part1`, `-part2`, … before that happens: by volume count (10 000) and by
  accumulated `volumes.txt` bytes (900 KiB per part — margin under the 1 MiB
  hard limit, not the limit itself), whichever comes first. Each part is its
  own Job and ConfigMap.
- **A single shell argument** (B86): `IMAGES` is exported as one environment
  value that becomes part of the wrapper process's argument/environment
  space, and Linux caps that at 128 KiB total (`ARG_MAX`). 2 000 URLs at 100
  characters each is already 200 KB — over the limit on one volume alone,
  regardless of the ConfigMap byte budget above. As of this writing
  `validate` does not catch this case
  ahead of time: the pod dies with `Argument list too long` before the
  wrapper ever starts, rather than being rejected at `validate` with a
  sentence naming the volume — B86 records this as a live-run finding not
  yet closed. Splitting an oversized `images:` volume into several smaller
  ones, or giving it a real IIIF manifest instead, avoids it today.

**Reading the file yourself.** No need to run the wrapper or write a
campaign file — the ConfigMap is a normal cluster object:

```console
$ kubectl get configmap campaign-trolldomskommissionen -n htr-batch \
    -o jsonpath='{.data.volumes\.txt}'
R0001203	https://lbiiif.riksarkivet.se/arkis!R0001203/manifest
loose-scans	images:https://example.org/scan1.jpg,https://example.org/scan2.jpg
```

(the `\.` escapes the literal dot in the key name `volumes.txt`, which
`jsonpath` would otherwise read as a nested field lookup).

## Wrapper env vars

The contract lives in one place: the
[Wrapper reference](../reference/wrapper.md#environment-contract) (defaults
from `packages/wrapper/src/htrflow_batch/config.py`). Required:
`VOLUME_REF`, `IIIF_MANIFEST_URL`, `PIPELINE_PATH`, `PIPELINE_ID`,
`S3_BUCKET`, `PUBLIC_RESULTS_BASE`; credentials come from the mounted S3
Secret file, never from env. The knobs you touch for a hand-run experiment:

| Env | Meaning | Default |
|---|---|---|
| `MAX_PAGES` | cap on pages processed, `0` = all — the test knob | 0 |
| `MAX_IMAGE_WIDTH` | IIIF size cap (`/full/{w},/`), enforced and part of the fetched URL | 2500 |
| `RESUME` | skip pages whose PAGE + ALTO already exist | true |
| `MANIFEST_MAX_BYTES` / `FETCH_MAX_BYTES` | byte caps on the manifest / one image | 16 MiB / 64 MiB |
| `LOG_SHIP_SECONDS` | live run-log upload interval, `0` = final only | 15 |

See [The Wrapper](../how-it-works/wrapper.md) for the streaming design
behind these knobs.

## Exit codes

| Code | Meaning | Reaction |
|---|---|---|
| 0 | success (verified) | index `Complete`; `manifest.json` in S3 = done |
| 13 | permanent (bad manifest URL / 4xx / non-JSON / empty, bad pipeline YAML, unknown step or model) | `podFailurePolicy` fails the index at once (`FailIndex`) — never retried |
| 1 | transient (network, 5xx on the manifest, CUDA hiccup, verification gap, S3 outage) | Kubernetes retries the index up to `backoffLimitPerIndex` (default 3); resume makes it cheap |
| 143 | SIGTERM (drain that reaches the container) after writing the termination log and shipping the log | retried the same as exit 1 — pages already published are not redone |

Failures write a structured reason to `/dev/termination-log`
(`{"stage": "stream", "permanent": false, "error": "verify failed: N missing, M failed errors: … missing=[…]"}`)
so `kubectl describe pod` and the captured failure log show *why* without
log spelunking; the full matrix is in
[Failure Handling](../how-it-works/failure-handling.md). `MAX_PAGES` is your
test knob for a fast end-to-end check before submitting a full-size volume —
cap it to 1 or a handful of pages, verify the output, then submit the real
Job with `MAX_PAGES=0`.

## Local compose alternative

No Kubernetes cluster needed to exercise the wrapper end to end:

```bash
make compose-up      # background stack: S3 (RustFS) + fixtures + wrapper + viewer
make compose-smoke   # foreground: runs the wrapper to completion, then smoke-checks the viewer
make compose-down
```

`make compose-smoke` is the verified end-to-end path on this repo — it
builds the wrapper image fresh, waits for it to exit, brings up the
viewer, and curls `http://localhost:8080/uv.html`. (`make compose-test`
drives the same stack through dagger, but needs registry-pullable images,
so treat `compose-smoke` as the default local check.)

The wrapper service in `.docker/docker-compose.yml` sets `MAX_PAGES: "1"`
by default — CPU is roughly 41× slower than GPU, so the compose stack only
processes one page of the mock volume rather than the whole thing. RustFS
is published on host ports **19000**/**19001** (not the RustFS default
9000/9001, to avoid colliding with an unrelated MinIO on some hosts), and
the viewer on host port **8080**. The stack's throwaway RustFS credentials
come from `.env.example` (`HTR_DEV_S3_*`).
