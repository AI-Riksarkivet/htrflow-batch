# Testing

## Acceptance levels in detail

0. **Library-API pin test** — import `Pipeline.from_config` and run a 1-page
   fixture against the exact htrflow version in the pinned image; the canary
   that a version bump broke the [D16 driver](../how-it-works/wrapper.md)
   (fall back to L1/L2 if so). Opt-in, because it needs the model weights:
   `make test-driver-real` locally, `dagger call test-driver` in CI, both
   running `packages/wrapper/tests/test_driver.py` inside the built wrapper
   image. `driver.py` keeps every htrflow import function-local so the
   ordinary suite (level 1) runs without torch against fakes.
1. **Unit tests** — wrapper: manifest walking (P2/P3, sized requests, the
   400 → `max` fallback), fetch acceptance (raster magic, textual
   content-types, byte caps, partial-file unlink), resume-list diffing incl.
   `page_sources`, the **streaming loop** (consumer starvation accounting,
   per-page failure propagation, rolling delete, `UploadOutage`), the
   **verification gate** (missing output ⇒ no `manifest.json`, transient
   exit), exit-code mapping incl. SIGTERM, log shipping, warm-up
   classification. Reconciler: parse (ids, allow-list, revisions, http(s)
   only), `derive` for every state, the tick against a fake bucket and
   cluster (Lease, retries, sticky verdicts, progress rule, warm-up cap,
   bounded validation, orphans, fairness), jobspec, guards, attempts
   migration, dulwich checkout against a local repo. Frontend: schemas
   (fail-soft, URL refusal, `unknown`), derivation, run-log grouping,
   component and route tests on jsdom.
2. **Container smoke** — the batch image against a real 2-page manifest with
   a RustFS target; assert PAGE + ALTO files + `manifest.json` land.
3. **Cluster acceptance** —
   a. 1 small volume end-to-end;
   b. ~10 volumes: never more than quota running, rest suspended, all
      eventually Complete, one `manifest.json` each;
   c. kill a running pod mid-volume: retry resumes, converges, no
      duplicate/corrupt outputs;
   d. a campaign through the reconciler: declared in git, submitted,
      watched on the campaign browser with its live log;
   e. the fetch-vs-HTR numbers from the published `manifest.json`s
      ([Phase 2](../roadmap/phase-2-cache.md) gate input).

Level 1 runs in seconds and is enforced on every change; level 2 is the
local compose stack; level 3 needs a real (or PoC) cluster and is what
produced the [test log](test-log.md).

## How to run each level

**Level 1 — unit tests:**

```bash
make test                       # uv run --all-packages pytest -q
cd frontend && bun run test     # vitest
# or, reproducibly, the way CI runs it:
dagger call test                # add --ca-bundle on TLS-intercepting networks
```

`make test` runs both Python packages — **471 tests** (+1 opt-in `htrflow`-marked)
as of 2026-08-27 (154 wrapper + 317 reconciler); the frontend suite is **76 tests in 10
files**. `dagger call test` runs the Python suite inside a uv container
with `uv sync --all-packages`, which pins the dependency resolution but
says nothing about the production images — those are built separately by
`dagger call build` / `dagger call build-viewer`. `make typecheck` (`ty`)
is a separate gate; run it before pushing (see [CI](ci.md)).

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
    published `riksarkivet/htrflow-batch-viewer:latest` (and any PoC
    registry tag from before 2026-08) are still the old port-80 images, so
    the viewer step of the smoke will fail against them. Build and tag
    locally first:

    ```bash
    make viewer-image
    docker tag 127.0.0.1:30500/uv4:dev riksarkivet/htrflow-batch-viewer:latest
    ```

**Chart:** `make helm-template` lints and renders the chart on its defaults
and on `charts/htrflow-batch/ci/full-values.yaml` (every optional feature on,
no cluster lookups) and runs `kubeconform -strict` when it is installed.

**Level 3 — cluster acceptance:** no single make target — this is a real (or
PoC) Kubernetes cluster with the [helm chart](../getting-started/deploy.md)
installed, exercised via `kubectl`/`k9s` and the campaign browser as
described in [Running a Campaign](../getting-started/campaigns.md) and
[Local k3s development](local-k3s.md). See the [test log](test-log.md)
for the exact commands and results from the 2026-07-27/28 and 2026-08-25
runs.
