# Testing

## Acceptance levels in detail

0. **Library-API pin test** — the real `Pipeline.from_config`, `Export`,
   `auto_import` and `Pipeline.run` on a one-page CPU fixture, against the
   htrflow inside the built wrapper image; the canary for an htrflow bump
   that breaks the [driver](../how-it-works/wrapper.md). No model is
   loaded — a binarization step exercises the step, document and serializer
   path — so it runs offline in seconds. It needs the wrapper image, so it
   runs in the one CI job that has already built one, straight after that
   build (`make test-driver-real` against the image it just made);
   `dagger call test-driver` builds its own and is the way to run it
   anywhere else. All three run `packages/wrapper/tests/test_driver_real.py`
   inside the image. `driver.py` keeps every htrflow import function-local, so the
   ordinary suite (level 1, `test_driver.py`) runs without torch against
   fakes.
1. **Unit tests** — wrapper: manifest walking (IIIF Presentation 2 and 3,
   sized requests, the 400 → `max` fallback), fetch acceptance (raster
   magic, textual content types, byte caps, partial-file unlink), resume-list
   diffing including `page_sources`, the **streaming loop** (consumer
   starvation accounting, per-page failure propagation, rolling delete,
   `UploadOutage`), the **verification gate** (missing output ⇒ no
   `manifest.json`, transient exit), exit-code mapping including SIGTERM,
   log shipping, warm-up classification, the synthetic-manifest builder.
   Converter: parse (ids, http(s) only, append-only, the refusal of a
   chart-owned key in `converter.yaml`), render (golden fixture → expected
   ConfigMap/Job YAML), the 10 000-volume split, and the chart-agreement test
   that asserts `docs/reference/configuration.md` equals what
   `make config-reference` generates. Web front: `projection.py`'s pure
   functions against hand-built Job/Pod/ConfigMap dicts (phase derivation,
   index-range parsing, per-volume state, termination messages, warm-up
   matching) plus the route and static-mount tests — no fixture cluster
   needed. Frontend: schemas, derivation, the ALTO parser, run-log grouping,
   and component and route tests on jsdom.
2. **Container smoke** — the batch image against a real two-page manifest
   with a RustFS target; assert PAGE and ALTO files and `manifest.json` land.
3. **Cluster acceptance** —
   a. one small volume end to end;
   b. about ten volumes: never more than quota running, the rest suspended,
      all eventually Complete, one `manifest.json` each;
   c. kill a running pod mid-volume: the retry resumes and converges, with
      no duplicate or corrupt outputs;
   d. a campaign rendered and applied: declared in git,
      `htrflow-campaigns render` and apply, watched on the campaign browser
      with its live log;
   e. the fetch-vs-HTR numbers from the published `manifest.json` files,
      the evidence the [cache layer](../roadmap/cache-layer.md) proposal
      waits on.

Level 1 runs in seconds and gates every change; level 2 is the local compose
stack; level 3 needs a real GPU cluster.

## How to run each level

**Level 1 — unit tests:**

```bash
make test                       # uv run --all-packages pytest -q
cd frontend && bun run test     # vitest
# or, reproducibly, the way CI runs it:
dagger call test                # add --ca-bundle <file> behind a TLS-inspecting proxy
```

`make test` runs the three Python packages (wrapper, converter, web); the
root `pyproject.toml`'s `testpaths` names exactly those three. `dagger call
test` runs the same suite inside a uv container after `uv sync --frozen
--all-packages`, which pins the dependency resolution but says nothing about
the production images — those are built separately by `dagger call
build-wrapper` and `build-web` ([Releasing](releasing.md)). `make typecheck`
(`ty`) is a separate gate; run it before pushing ([CI](ci.md)).

**Level 2 — container smoke, through the local compose stack:**

```bash
make compose-up      # background: S3 (RustFS) + fixtures + wrapper + web front
make compose-smoke   # foreground: runs the wrapper to completion, then
                     # checks that the web image serves /uv.html
make compose-down
```

`make compose-smoke` builds the web image from the checkout and tags it
`riksarkivet/htrflow-web:latest`, builds the wrapper image and waits for it
to exit, then brings the web service up and fetches its `/uv.html` — the
default local check. The compose `web` service is deliberately image-only:
`dagger call compose-test` drives the same stack but mounts only `.docker/`
as the compose project, where a `build:` context of `..` cannot resolve, so
it pulls the published web image the compose file pins by digest.
`compose-smoke` builds that image locally instead. The stack itself is
described in [Try it](../getting-started/try-it.md).

The web service runs site-only in both (`HTRFLOW_WEB_SITE_ONLY=1`): a compose
stack has no API server, so `/api/v1/…` answers 503 by design and the site is
what this level checks.

**Chart:** `make helm-template` lints and renders both charts
(`charts/htrflow-batch`, `charts/htrflow-devstack`) on their defaults and on
each chart's `ci/full-values.yaml` (every optional feature on, no cluster
lookups), checks that the devstack chart refuses RustFS without chosen
credentials, and runs `kubeconform -strict` when it is installed.

**Campaigns repo shape:** `htrflow-campaigns validate examples/campaigns`
must pass — `packages/converter/tests/test_cli.py` runs it, so the checked-in
example repo cannot drift from the converter it demonstrates.

**Level 3 — cluster acceptance:** no single make target. It needs a
Kubernetes cluster with GPU nodes and the chart
[deployed](../getting-started/deploy.md), exercised with `kubectl` or `k9s`
and the campaign browser as described in
[Run a campaign](../getting-started/campaigns.md) and
[Dev cluster](dev-cluster.md). `make e2e DIR=<campaigns-repo>` automates the
happy path: validate, render and apply, then block until every campaign Job
reaches a terminal condition. Kill-and-resume (c) is by hand: once a few
ALTO files exist under a volume's prefix, force-delete the running pod and
watch the retry pod log `resume: <n> done, <m> to process` and converge to
`Complete`.
