# Deployment

## Building images

Both images build reproducibly through the dagger module — no local
Dockerfile invocations needed for CI, though `.docker/htrflow-batch.dockerfile`
and `make poc-push` remain for fast local iteration:

```bash
dagger call build                  # wrapper image, from .docker/htrflow-batch.dockerfile
dagger call build-viewer           # UV4 viewer image, pinned upstream ref + patch
```

`dagger call build` is heavy the first time — the base
(`airiksarkivet/htrflow` + cu128 torch) is ~10 GB — but the dagger engine
cache makes subsequent builds fast. `build-viewer` clones the Riksarkivet
`universalviewer4` fork at a pinned commit, applies
`.docker/uv4-uv-html.patch`, runs `npm build`, and layers the result onto
`nginxinc/nginx-unprivileged:1.27-alpine` (with the bun-built campaign browser
SPA on top). See [CI](ci.md) for the full function table.

For fast local-only iteration against a bare-k3s PoC's in-cluster registry
(no dagger, no push credentials):

```bash
make poc-push
# = docker build -f .docker/htrflow-batch.dockerfile -t 127.0.0.1:30500/htrflow-batch:dev .
#   docker push 127.0.0.1:30500/htrflow-batch:dev
```

## Publishing

```bash
dagger call publish-docker --component wrapper \
  --docker-username env:DOCKERHUB_USERNAME --docker-password env:DOCKERHUB_TOKEN
```

`--component` is `wrapper` (default) or `viewer`. `publish-docker` runs the
test suite first and aborts on failure — it will not push an image the tests
don't pass. Tag resolution: an explicit `--tag` is validated against
`packages/wrapper/pyproject.toml`'s version unless `--skip-validation` is set; an
empty tag defaults to `"v" + <that version>`.

**Registry defaults:**

| Component | Default repository | Default registry |
|---|---|---|
| wrapper | `riksarkivet/htrflow-batch` | `docker.io` |
| viewer | `riksarkivet/htrflow-batch-viewer` | `docker.io` |

Override with `--image-repository` / `--registry`. In CI, `publish.yml` is
manual (`workflow_dispatch`) only and supplies `DOCKERHUB_USERNAME` /
`DOCKERHUB_TOKEN` from repository secrets.

## Chart release notes

The chart (`charts/htrflow-batch`) is not yet published to a chart
repository — there is no packaging/release workflow for it. Install directly
from a checkout:

```bash
helm install htr charts/htrflow-batch -n htr-batch --create-namespace \
  --set image.repository=<registry>/htrflow-batch --set image.tag=<pinned-digest-or-tag> \
  ...
```

`make helm-lint` (`helm lint charts/htrflow-batch`) runs as part of `dagger
call checks`, so lint failures block CI the same way ruff failures do. If
the chart is ever published, `Chart.yaml`'s `icon` field is currently absent
(flagged by `helm lint` as merely "recommended," not an error) — worth
filling in before a public release. See [Deploy](../getting-started/deploy.md)
for the full install flow, including the production-shaped install and the
PoC devStack replay.
