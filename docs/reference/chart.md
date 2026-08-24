# Chart Values

`charts/htrflow-batch` — one chart for the Kueue queue objects, the viewer,
the reconciler, and the PoC dev stack.
Source: [`charts/htrflow-batch/values.yaml`](https://github.com/carpelan/test/blob/main/charts/htrflow-batch/values.yaml)

## Core

| Key | Default | Description |
|-----|---------|-------------|
| `image.repository` / `image.tag` | `docker.io/riksarkivet/htrflow-batch` / `latest` | Wrapper image for the example Job; campaign Jobs use the digest pinned in the **pipeline YAML**, not this |
| `s3.existingSecret` | `htr-batch-s3` | Secret with `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_ENDPOINT`, `S3_BUCKET` — injected via `envFrom` into Jobs and the reconciler |
| `s3.endpoint` | `""` | Informational only; the endpoint the pods use comes from the secret |
| `s3.bucket` | `htr-results` | Used by the devStack RustFS bootstrap only |
| `publicResultsBase` | `""` | **Required** — browser-reachable URL base for published results (viewer manifests embed it) |

## Queue (`queue.*`)

| Key | Default | Description |
|-----|---------|-------------|
| `queue.name` | `htr-batch` | ClusterQueue + LocalQueue name |
| `queue.flavor` | `default-flavor` | ResourceFlavor |
| `queue.resources` | cpu 2 / memory 4Gi | Covered quotas — every resource a Job requests must be listed or Kueue marks it inadmissible; add `nvidia.com/gpu` on real clusters |

## Pipelines (`pipelines`)

Map of `id → htrflow pipeline YAML string`, rendered as immutable ConfigMaps
`htr-pipeline-<id>`. Never change content under an existing id — mint a new
one. Campaign pipelines are managed by the reconciler from git instead; this
value is for hand-run Jobs.

## Viewer (`viewer.*`)

| Key | Default | Description |
|-----|---------|-------------|
| `viewer.enabled` | `true` | Deploy the viewer (campaign browser at `/`, UV4 at `/uv.html`) |
| `viewer.image` | `docker.io/riksarkivet/htrflow-batch-viewer:latest` | nginx-unprivileged image, listens on 8080 |
| `viewer.nodePort` | `30800` | NodePort service |
| `viewer.defaultManifest` | `""` | Deprecated: when set, `/` redirects into UV instead of the campaign browser |

## Reconciler (`reconciler.*`)

Renders a ServiceAccount, Role, RoleBinding and the `htr-reconciler` CronJob
(`concurrencyPolicy: Forbid`, `activeDeadlineSeconds: 240`, `backoffLimit: 0`,
namespace via downward API, S3 creds via `envFrom` from `s3.existingSecret`).

| Key | Default | Description |
|-----|---------|-------------|
| `reconciler.enabled` | `false` | Deploy the CronJob + RBAC |
| `reconciler.image` | `""` | Reconciler image (PoC: `127.0.0.1:30500/htrflow-reconciler:dev`) |
| `reconciler.campaignsRepoUrl` | `""` | HTTPS git URL of the campaigns repo — must be anonymously clonable |
| `reconciler.schedule` | `*/5 * * * *` | Tick cadence |
| `reconciler.window` | `20` | Max in-flight (not yet terminal) Jobs |
| `reconciler.attemptCap` | `3` | Retries per (pipeline, volume) |
| `reconciler.publicResultsBase` | `""` | Defaults to the global `publicResultsBase` |

## Dev stack (`devStack.*`) — PoC only, all off by default

| Key | Default | Description |
|-----|---------|-------------|
| `devStack.rustfs.enabled` | `false` | In-cluster S3 (RustFS), NodePorts 30900 (S3) / 30901 (console) |
| `devStack.registry.enabled` | `false` | In-cluster image registry, NodePort 30500 |
| `devStack.nvidiaDevicePlugin.enabled` | `false` | NVIDIA device plugin DaemonSet |

## Example Job (`exampleJob.*`)

| Key | Default | Description |
|-----|---------|-------------|
| `exampleJob.enabled` | `false` | Render a smoke-test Job wired to the devStack endpoints |
| `exampleJob.image` / `manifestUrl` / `pipelineId` | `""` / `""` / `demo-v1` | Image, source manifest, and pipeline ConfigMap for it |
