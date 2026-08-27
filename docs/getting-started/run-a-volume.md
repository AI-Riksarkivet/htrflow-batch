# Run a Volume

The normal way to run a volume is to declare it in the campaigns repo and let
the reconciler submit it ([Running a Campaign](campaigns.md)). This page is
about what a single volume Job *is*, for hand-run experiments and for
reading what the reconciler creates.

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
  backoffLimit: 0
  podFailurePolicy:
    rules:
      - action: Ignore
        onPodConditions: [{ type: DisruptionTarget }]
      - action: FailJob
        onExitCodes: { containerName: wrapper, operator: In, values: [13] }
  activeDeadlineSeconds: 21600      # the reconciler derives it from the page count
  ttlSecondsAfterFinished: 86400
```

Submit 200 volumes and exactly N run at once (N = queue quota); the rest
wait in FIFO order (`kubectl get workloads -n htr-batch`). No preemption,
no cohorts in Phase 1. The complete spec the reconciler builds — resources,
mounts, labels, hardening — is in
[The Wrapper → Job template](../how-it-works/wrapper.md#job-template-one-volume-one-job);
`charts/htrflow-batch/templates/job-example.yaml` (`exampleJob.enabled`) is
a rendered copy for the PoC smoke test.

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
| 0 | success (verified) | Job `Complete`; `manifest.json` in S3 = done |
| 13 | permanent (bad manifest URL / 4xx / non-JSON / empty, bad pipeline YAML, unknown step or model) | `podFailurePolicy` fails the Job at once — the reconciler parks the volume as `needs-attention`, never retried |
| 1 | transient (network, 5xx on the manifest, CUDA hiccup, verification gap, S3 outage) | the reconciler retries up to the attempt cap (default 3); resume makes it cheap |
| 143 | SIGTERM (Job deadline, drain) after writing the termination log and shipping the log | retried; not charged an attempt when pages advanced |

Failures write a structured reason to `/dev/termination-log`
(`{"stage": "stream", "permanent": false, "error": "verify failed: missing=[…]"}`)
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
