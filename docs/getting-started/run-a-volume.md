# Run a Volume

## The Job contract

One archival volume = one Kubernetes Job. Jobs start `suspend: true` and
carry the Kueue queue label; Kueue unsuspends them as quota frees:

```yaml
kind: Job
metadata:
  labels:
    kueue.x-k8s.io/queue-name: htr-batch
spec:
  suspend: true
```

Submit 200 volumes and exactly N run at once (N = queue quota); the rest
wait in FIFO order (`kubectl get workloads -n htr-batch`). No preemption,
no cohorts in Phase 1.

## Wrapper env vars

| Env | Meaning | Default |
|---|---|---|
| `VOLUME_REF` | archival reference code (S3 prefix, logging) | required |
| `IIIF_MANIFEST_URL` | manifest to process (resolved by CLI at submit time) | required |
| `PIPELINE_PATH` | pipeline YAML, mounted from the immutable per-version ConfigMap | required |
| `PIPELINE_ID` | short id namespacing the output keys | required |
| `S3_ENDPOINT` / `S3_BUCKET` / `S3_PREFIX` | result destination (creds from Secret) | required |
| `MAX_IMAGE_WIDTH` | IIIF size cap (`/full/{w},/`) — **enforced**, and part of the fetched URL, so cached/stored artifacts can never disagree with config (note: `!w,h` 501s on lbiiif) | 2500 |
| `RESUME` | skip pages whose outputs already exist | true |
| `LOOKAHEAD_PAGES` | max pages downloaded ahead of the consumer (bounds tmpfs) | 64 |
| `MAX_PAGES` | cap on pages processed, `0` = all (test knob) | 0 |
| `WORKDIR_PATH` | filesystem path for downloads + local pipeline outputs | /work |
| `DOWNLOAD_CONCURRENCY` | concurrent image downloads | 12 |
| `PUBLIC_RESULTS_BASE` | browser-reachable base URL for `iiif.json`/viewer links (≠ the in-cluster S3 endpoint) | required |
| `TERMINATION_LOG_PATH` | where the exit reason (stage, permanent/transient, error) is written | /dev/termination-log |

See [The Wrapper](../how-it-works/wrapper.md) for the full streaming design
behind these knobs.

## Exit codes

| Code | Meaning | Job reaction |
|---|---|---|
| 0 | success (verified) | Complete |
| 13 | permanent (bad manifest URL, bad pipeline YAML, volume exceeds budget) | `FailJob` — no retry |
| other | transient (network, CUDA hiccup, verification gap) | retry within `backoffLimit` |

Failures write a structured reason to `/dev/termination-log`
(`{"stage": "fetch", "page": 412, "error": ...}`) so `htrq status` shows
*why* without log spelunking. `MAX_PAGES` is your test knob for a fast
end-to-end check before submitting a full-size volume — cap it to 1 or a
handful of pages, verify the output, then submit the real Job with
`MAX_PAGES=0`.

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
the viewer on host port **8080**.
