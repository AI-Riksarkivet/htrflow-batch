# Running a Campaign

How to go from "the chart is installed" to "a campaign is transcribing itself
and I can watch it". The mechanics behind each step are in
[Campaigns (GitOps)](../how-it-works/campaigns.md).

Prerequisites: the chart deployed ([Deploy](deploy.md)), the S3 secret in
place, and a browser-reachable `publicResultsBase`.

## 1. Create the campaigns repo

Desired state lives in its own repo, `htr-campaigns`. The scaffold — README,
one pipeline file, one campaign file, the immutability guard script and its
workflow — is generated locally at `~/htr-campaigns` with `main` already
committed, but **no remote**:

```
README.md
campaigns/htr-demo-examples.yaml
pipelines/demo-v1.yaml
scripts/check_immutable.sh
.github/workflows/guard.yml
```

!!! danger "User action: create the repo and protect `main`"

    The reconciler cannot clone anything until the repo exists on GitHub:

    ```bash
    cd ~/htr-campaigns
    git remote add origin https://github.com/<org>/htr-campaigns
    git push -u origin main
    ```

    Then **enable branch protection on `main` with required review,
    immediately**. Write access to this repo is effectively code execution on
    the GPU node (pipeline YAML selects arbitrary Hugging Face model repos),
    and the immutability guard only runs on `pull_request` — a direct push
    bypasses it entirely. Until protection is on, guard 1 is advisory.

## 2. Pin an image digest

Every pipeline file must name the wrapper image by digest; tags are rejected.
After pushing the image, read the digest back:

```bash
docker inspect --format '{{index .RepoDigests 0}}' <registry>/htrflow-batch:<tag>
```

and paste it into `pipelines/<id>.yaml`:

```yaml title="pipelines/demo-v1.yaml"
image: <registry>/htrflow-batch@sha256:34d462b8de7dccda...
steps:
  - step: Segmentation
    ...
```

!!! warning "User action: publish to a real registry before any non-PoC campaign"

    The scaffold pins a digest in the in-cluster PoC registry
    (`127.0.0.1:30500/…`), which only resolves from the PoC nodes. Anything
    beyond the PoC needs the image pushed to a real registry (GHCR, Docker
    Hub) and re-pinned. Because pipeline files are immutable once results
    exist under their id, re-pinning means a **new pipeline id and a new
    campaign file**, not an edit.

## 3. Enable the reconciler

The reconciler ships in this chart and is off by default. Turn it on with the
campaigns repo URL and the reconciler image:

```bash
helm upgrade --install htr charts/htrflow-batch -n htr-batch \
  --set reconciler.enabled=true \
  --set reconciler.image=<registry>/htrflow-reconciler:<tag> \
  --set reconciler.campaignsRepoUrl=https://github.com/<org>/htr-campaigns \
  --set publicResultsBase=<browser-reachable results base URL>
```

| Value | Default | What it does |
|---|---|---|
| `reconciler.enabled` | `false` | renders the CronJob, ServiceAccount, Role and RoleBinding |
| `reconciler.image` | `""` | **required** when enabled |
| `reconciler.campaignsRepoUrl` | `""` | **required**; cloned shallow each tick over HTTPS |
| `reconciler.schedule` | `*/5 * * * *` | tick interval (`concurrencyPolicy: Forbid`) |
| `reconciler.window` | `20` | max not-yet-done Jobs in flight across all campaigns |
| `reconciler.attemptCap` | `3` | retries per (pipeline, volume) before `needs-attention` |
| `reconciler.publicResultsBase` | inherits `publicResultsBase` | URL base stamped into manifests and viewer links |

RBAC is namespace-scoped: Jobs, ConfigMaps, Pods and pod logs in the release
namespace, nothing cluster-wide.

!!! note "Changing `reconciler.schedule` desyncs the STALE banner"

    The reconciler emits a hardcoded `tick_seconds: 300` into `status.json`,
    and the page's STALE threshold is 3× that. The banner's math is therefore
    only honest on the default `*/5 * * * *` schedule; a slower schedule will
    flag STALE while everything is fine (and a faster one will hide a dead
    reconciler for longer) until the schedule is wired through to
    `tick_seconds`.

Check the first tick:

```bash
kubectl -n htr-batch get cronjob htr-reconciler
kubectl -n htr-batch logs --tail=50 \
  "$(kubectl -n htr-batch get jobs --sort-by=.metadata.creationTimestamp -o name \
     | grep htr-reconciler | tail -1)"
```

The CronJob's Jobs are named `htr-reconciler-<timestamp>`; campaign Jobs share
the namespace, hence the `grep`. (Jobs carry no `job-name` label — that one is
on their pods.)

## 4. Add work — it is a commit

```yaml title="campaigns/trolldomskommissionen.yaml"
pipeline: demo-v1
volumes:
  - R0001203
  - id: dodsbok-1698
    manifest: https://iiif.example.org/xyz/manifest
```

Open a PR, get it reviewed, merge. Within one tick the reconciler submits
whatever is missing, up to the window. Nothing else is needed — and there is
no other way in: the status page is read-only by construction.

Removing a volume stops future work on it and never deletes results; the
volume simply shows up as an **orphan** on the page afterwards.

## 5. Watch it

The campaign browser is the viewer's front door:

```
http://<node>:30800/
```

The same container serves the Universal Viewer at `/uv.html`, and clicking a
volume card opens it there — the published `iiif.json` with text overlay for a
done volume, the source manifest (images only, no transcription yet) for
everything else.

!!! note "Reaching it from a laptop"

    On the bare-k3s PoC both NodePorts must be tunnelled — the page comes from
    30800, the status document and images from 30900. See
    [Viewing Results](viewing.md#reaching-the-viewer-over-ssh-poc-bare-k3s).

What to read on the page:

| Signal | Means |
|---|---|
| red **STALE** banner | `status.json` is older than 3 ticks — the reconciler is failing or dead. It is **not** "no news": check the CronJob and its last Job's logs |
| campaign chip `broken` | the campaign YAML failed to parse, or names an unknown pipeline; every other campaign kept going |
| top-level warning line | pipeline drift (nothing submitted for that pipeline until resolved) or a grandfathered pre-pinning result |
| `orphaned results` line | volumes present in the bucket but no longer in git |
| `needs-attention` chip | exit 13 (permanent) or the retry budget is spent; the captured logs are at `status/failures/<pipeline>/<volume>.txt` in the bucket |
| `unsupported` / `unreachable` chip | pre-validation rejected the manifest, so no Job was burned. `unreachable` is re-probed every tick; `unsupported` is cached |

If the page cannot load status at all, check that `status/status.json` is
public-read and CORS-open — the fetch is cross-origin — and that the SPA is
pointed at it (it reads `window.STATUS_URL`, defaulting to the PoC's
`http://localhost:30900/htr-results/status/status.json`).

## Rebuilding the viewer image

The SPA is baked into the viewer image next to UV4, so any UI change needs a
rebuild:

```bash
make viewer-image                # local: bun build + docker build, tags 127.0.0.1:30500/uv4:dev
dagger call build-viewer         # reproducible: clones + patches UV, bun-builds the SPA
```

The image runs unprivileged and listens on **8080** (the chart's
`containerPort` and Service `targetPort` follow; NodePort 30800 is unchanged).

!!! info "`viewer.defaultManifest` is deprecated"

    It predates the campaign browser: when set, `/` 302-redirects to a single
    manifest in UV instead of serving the browser. Leave it empty unless you
    deliberately want the old single-volume front door.
