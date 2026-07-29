# Testing

## Acceptance levels in detail

0. **Library-API pin test** — import `Pipeline.from_config` and run a 1-page
   fixture against the exact htrflow version in the pinned image; this is the
   canary that a version bump broke the [D16 driver](../how-it-works/wrapper.md)
   (fall back to L1/L2 if so).
1. **Wrapper unit tests** — manifest walking, filename ordering, resume-list
   diffing, **streaming loop** (consumer starvation accounting, per-page
   failure propagation, rolling delete, uploader ordering), **verification
   gate** (missing output ⇒ no `manifest.json`, transient exit), exit-code
   mapping; mocked HTTP + S3 (moto or a throwaway MinIO).
2. **Container smoke** — `docker run` the batch image against a real 2-page
   manifest with a MinIO/RustFS target; assert ALTO files + `manifest.json`
   land.
3. **Cluster acceptance** —
   a. 1 small volume end-to-end;
   b. ~10 volumes: never more than quota running, rest suspended, all
      eventually Complete, one `manifest.json` each;
   c. kill a running pod mid-volume: retry resumes, converges, no
      duplicate/corrupt outputs;
   d. `htrq report` produces the fetch-vs-HTR numbers ([Phase 2](../roadmap/phase-2-cache.md)
      gate input).

Levels 0–1 run in seconds and are enforced on every change; level 2 is the
local compose stack; level 3 needs a real (or PoC) cluster and is what
produced the [test log](test-log.md).

## How to run each level

**Level 0–1 — wrapper unit tests:**

```bash
make test                       # uv run --no-sync pytest packages/wrapper/tests -q
# or, reproducibly, the way CI runs it:
dagger call test                # add --ca-bundle on TLS-intercepting networks
```

Both run the same pytest suite (47 tests as of this writing); `dagger call
test` builds the wrapper in a container first, so it also proves the
container image builds cleanly.

**Level 2 — container smoke, via the local compose stack:**

```bash
make compose-up      # background: S3 (RustFS) + fixtures + wrapper + viewer
make compose-smoke   # foreground: runs the wrapper to completion, then
                      # smoke-checks the viewer serves uv.html
make compose-down
```

`make compose-smoke` builds the wrapper image fresh, waits for it to exit,
brings up the viewer, and curls `http://localhost:8080/uv.html` — this is the
verified default local check. `dagger call compose-test` drives the same
stack through dagger, but needs registry-pullable images, so treat
`compose-smoke` as the everyday path (see [CI](ci.md) for the caveat).

!!! warning "The viewer image must be built from this branch"

    Since the campaign browser landed, the viewer is `nginx-unprivileged`
    serving on **8080** with the SPA at `/` and UV at `/uv.html`. The
    published `riksarkivet/htrflow-batch-viewer:latest` (and the PoC
    registry's `uv4:v3`) are still the old port-80 images, so the viewer step
    of the smoke will fail against them. Build and tag locally first:

    ```bash
    make viewer-image
    docker tag 127.0.0.1:30500/uv4:dev riksarkivet/htrflow-batch-viewer:latest
    ```

**Level 3 — cluster acceptance:** no single make target — this is a real (or
PoC) Kubernetes cluster with the [helm chart](../getting-started/deploy.md)
installed, exercised via `kubectl`/`k9s`/`htrq` as described in
[Run a Volume](../getting-started/run-a-volume.md). See the [test log](test-log.md)
for the exact commands and results from the 2026-07-27/28 PoC run.
