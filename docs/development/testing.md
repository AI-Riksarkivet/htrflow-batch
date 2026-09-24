# Testing

## Acceptance levels in detail

0. **Library-API pin test** — the real `Pipeline.from_config`, `Export`,
   `auto_import` and `Pipeline.run` on a one-page CPU fixture, inside the
   built wrapper image: the canary for an htrflow bump that breaks the
   [driver](../how-it-works/wrapper.md). No model is loaded, so it runs
   offline in seconds, wherever a wrapper image was just built (CI's
   second-architecture job, the wrapper scan job, and `publish-docker` before
   the push). Locally: `make test-driver-real` or `dagger call test-driver`.
1. **Unit tests**, everything mocked. Wrapper: manifest walking (IIIF
   Presentation 2 and 3, sized requests, the 400 fallback to the largest size
   the image's `info.json` offers within the cap), fetch acceptance, resume,
   the streaming loop, the verification gate, exit-code mapping, log
   shipping, warm-up classification. Converter: parse, append-only, render
   against golden fixtures, the 10 000-volume split, the chart-agreement
   test. Read API: `projection.py`'s pure functions and the routes. Frontend:
   schemas, derivation, the ALTO parser and components on jsdom, plus
   property-based tests (fast-check, `*.property.test.ts`) for the URL
   guards, the run-log parser and the failure sentences: each run draws a
   fresh seed and prints it with a counterexample, and `FC_SEED=<seed>`
   replays that run.
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
cd .dagger && go vet ./publishcheck/ && go test ./publishcheck/   # the dagger module's Go test
```

The Go test reads the order of `publish-docker`'s gates from the dagger
module's syntax tree, so a gate commented out or moved after the push fails
it. It needs no engine; `ci.yml` runs it in a job of its own.

## The generated files CI checks are current

Three files in this repository are printed by a script and committed, and in
each case a test in the normal suite fails when the committed copy is not
what the script prints — so `dagger call test` (and therefore `make ci`)
catches a stale one without a job of its own.

| File | Regenerate with | The test that asserts it |
| --- | --- | --- |
| `docs/reference/configuration.md` | `make config-reference` | `packages/converter/tests/test_chart_agreement.py` |
| `frontend/src/lib/fixtures/api-contract.json` | `make api-contract` | `packages/web/tests/test_contract.py` |
| `frontend/src/lib/fixtures/wrapper-contract.json` | `make wrapper-contract` | `packages/wrapper/tests/test_contract.py` |

The two contract fixtures tie the Python side to the page with no copy in
between. `scripts/api_contract.py` asks the read API's routes over a fake
cluster (live, reaped and unknown campaigns, a failed volume, bucket
progress); `api-contract.test.ts` parses every row with the page's schemas
and fails when the page drops a field it does not mean to drop. `scripts/wrapper_contract.py`
calls the wrapper's own functions for a `manifest.json` and the termination
messages; the run viewer's schema and the failure-sentence tests read them.
Change a projection and the pytest fails; run `make api-contract` and the
vitest tells you whether the schemas can still read what you changed.

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

`make compose-smoke` builds both images from the checkout with the same
recipes as the images that ship (`build-wrapper`, `build-web`), runs the
wrapper to completion on them, then brings the web service up and fetches
its `/uv.html` — the default local check. The compose file takes the two
images from `HTR_WRAPPER_IMAGE` and `HTR_WEB_IMAGE`, which is how the smoke
runs what it built rather than the release the file pins. `make
compose-smoke-run WRAPPER_IMAGE=<ref> WEB_IMAGE=<ref>` runs the same smoke
on any two images, a published release by digest included. The stack is
torn down, volumes included, however the run ends. `dagger call compose-test`
drives the same stack with the published web image the compose file pins
by digest. The stack itself is described in
[Try it](../getting-started/try-it.md).

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
reaches a terminal condition. A Failed Job, or no campaign Job at all, fails
the target. Kill-and-resume (c) is by hand: once a few
ALTO files exist under a volume's prefix, force-delete the running pod and
watch the retry pod log `resume: <n> done, <m> to process` and converge to
`Complete`.
