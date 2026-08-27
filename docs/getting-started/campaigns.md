# Running a Campaign

How to go from "the chart is installed" to "a campaign is transcribing itself
and I can watch it". The mechanics behind each step are in
[Campaigns (GitOps)](../how-it-works/campaigns.md).

Prerequisites: the chart deployed ([Deploy](deploy.md)), the S3 secret in
place, a browser-reachable `publicResultsBase`, and the reconciler image
pushed somewhere the cluster can pull it.

## 1. Create the campaigns repo

Desired state lives in its own git repo. The reconciler needs exactly two
directories; everything else is convention:

```
campaigns/<campaign>.yaml      # pipeline: <id> + volumes:
pipelines/<id>.yaml            # image: <digest> + steps:
scripts/check_immutable.sh     # recommended: refuses edits to existing pipelines/*.yaml
.github/workflows/guard.yml    # recommended: runs it on pull_request
README.md
```

The formats are in [Campaign & Pipeline YAML](../reference/campaign-yaml.md).
The PoC's repo (`htr-test`) has that layout and can be copied; nothing in
this repo generates it.

!!! danger "User action: protect `main` before the reconciler clones it"

    Write access to this repo is effectively **cluster operator**: a
    pipeline file names the image that runs on the GPU with the bucket's
    write credentials, and the Hugging Face model repos the warm-up pod
    loads. **Enable branch protection on `main` with required review,
    immediately.** The immutability guard only runs on `pull_request` — a
    direct push bypasses it entirely. Then set
    `security.allowedImageRepos` in the chart so only images from your
    registry can be named at all
    ([Security → Trust boundary](../development/security.md#trust-boundary)).

## 2. Pin an image digest

Every pipeline file must name the wrapper image by digest; tags are rejected.
After pushing the image, read the digest back (`make poc-push` prints it, or):

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

With `security.allowedImageRepos` set, `<registry>/` must be one of the
listed prefixes; with `security.requireModelRevision`, every model needs a
40-hex `revision:`.

!!! warning "User action: publish to a real registry before any non-PoC campaign"

    The PoC pins digests in the in-cluster registry (`127.0.0.1:30500/…`),
    which only resolves from the PoC node. Anything beyond the PoC needs the
    image pushed to a real registry (GHCR, Harbor) and re-pinned. Because
    pipeline files are immutable once results exist under their id,
    re-pinning means a **new pipeline id and a new campaign file**, not an
    edit.

## 3. Enable the reconciler

The reconciler ships in this chart and is off by default. Turn it on with the
campaigns repo URL and a digest-pinned reconciler image:

```bash
helm upgrade --install htr charts/htrflow-batch -n htr-batch --reset-then-reuse-values \
  --set reconciler.enabled=true \
  --set reconciler.image=<registry>/htrflow-reconciler@sha256:<digest> \
  --set reconciler.campaignsRepoUrl=https://github.com/<org>/htr-campaigns \
  --set reconciler.campaignsRepoWebUrl=https://github.com/<org>/htr-campaigns \
  --set publicResultsBase=<browser-reachable results base URL> \
  --set security.allowedImageRepos='{<registry>/}'
```

| Value | Default | What it does |
|---|---|---|
| `reconciler.enabled` | `false` | renders the CronJob, ServiceAccount, Role and RoleBinding |
| `reconciler.image` | `""` | **required** when enabled; `@sha256:` unless `devStack.allowTagImages` |
| `reconciler.campaignsRepoUrl` | `""` | **required**; cloned shallow each tick over `https://` (anonymous, or a read-only token in `reconciler.gitTokenSecret`) or in-cluster `git://` |
| `reconciler.campaignsRepoWebUrl` | `""` | the link in the campaign browser's header (e.g. the branch on GitHub); falls back to the clone URL |
| `reconciler.schedule` / `tickSeconds` | `*/5 * * * *` / `300` | tick interval; keep the two equal — `tickSeconds` is what the STALE banner is computed from |
| `reconciler.window` | `20` | max Jobs in flight across all campaigns |
| `reconciler.attemptCap` | `3` | attempts per (pipeline, volume) — and per warm-up — before `needs-attention` |
| `reconciler.publicResultsBase` | inherits `publicResultsBase` | URL base stamped into manifests and viewer links |

Every other value (deadlines, validation bounds, byte caps, Job placement)
is in [Chart Values](../reference/chart.md#reconciler-reconciler). RBAC is
namespace-scoped: Jobs, ConfigMaps, Pods, pod logs and one Lease in the
release namespace, nothing cluster-wide.

Check the first tick:

```bash
kubectl -n htr-batch get cronjob htr-reconciler
kubectl -n htr-batch logs --tail=50 \
  "$(kubectl -n htr-batch get jobs --sort-by=.metadata.creationTimestamp -o name \
     | grep htr-reconciler | tail -1)"
```

The CronJob's Jobs are named `htr-reconciler-<timestamp>`; campaign Jobs share
the namespace, hence the `grep`. (Jobs carry no `job-name` label — that one is
on their pods.) A healthy tick ends with one line,
`tick: seconds=… s3_calls=… validations=… submitted=… retried=… warnings=…`.
To run a tick by hand, `kubectl create job --from=cronjob/htr-reconciler
tick-now -n htr-batch` — if the scheduled tick is still running, the manual
one finds the Lease held and exits without doing anything.

## 4. Add work — it is a commit

```yaml title="campaigns/trolldomskommissionen.yaml"
pipeline: demo-v1
volumes:
  - R0001203
  - id: dodsbok-1698
    manifest: https://iiif.example.org/xyz/manifest
```

Open a PR, get it reviewed, merge. Within one tick the reconciler warms the
pipeline's models (first time only), validates the manifests, and submits
whatever is missing, up to the window. Nothing else is needed — and there is
no other way in: the status page is read-only by construction.

Removing a volume stops future work on it and never deletes results; the
volume simply shows up as an **orphan** on the page afterwards.

## 5. Watch it

The campaign browser is the viewer's front door:

```
http://<node>:30800/
```

The same container serves the Universal Viewer at `/uv.html` — a volume's
`open` link goes there with the published `iiif.json` (text overlay) for a
done volume, `source` with the source manifest (images only) for everything
else — and the run viewer at `/log`, which follows a running volume's log
live and shows the per-page summary once it finishes.

!!! note "Reaching it from a laptop"

    On the bare-k3s PoC both NodePorts must be tunnelled — the page comes from
    30800, the status document, logs and images from 30900. See
    [Viewing Results](viewing.md#reaching-the-viewer-over-ssh-poc-bare-k3s).

What to read on the page:

| Signal | Means |
|---|---|
| red **STALE** banner | `status.json` is older than 3 ticks — the reconciler is failing or dead. It is **not** "no news": check the CronJob and its last Job's logs |
| header line `4.1 s · 12 S3 calls · …` | what the last tick cost (`tick_summary`); a tick that runs into its 600 s deadline is the thing to watch at scale |
| campaign chip `broken` | the campaign YAML failed to parse, or names an unknown pipeline; every other campaign kept going |
| top-level warning line | pipeline drift (nothing submitted for that pipeline until resolved), a pipeline refused by the image allow-list or revision rule, a warm-up in progress or parked, an empty allow-list, a grandfathered pre-pinning result, or a corrupt state file treated as absent |
| `orphaned results` line | volumes present in the bucket but no longer in git |
| `needs-attention` chip with a `capped` / `exit-13` tag | the retry budget is spent, or the wrapper said permanent (exit 13). The captured log is behind the `log` link (`status/failures/<pipeline>/<volume>.txt`). It stays parked until you clear its record in `status/attempts.json` or re-run it under a new pipeline id |
| `retry` / `deleting` chip | a failed attempt is being cleaned up; the volume re-enters the queue next tick with resume |
| `unsupported` / `unreachable` chip | pre-validation rejected the manifest, so no Job was burned. `unsupported` (and a 4xx) is cached for good; a transient `unreachable` is re-probed after 3 ticks |
| `planned` chip that never moves | either the window is full, or the manifest is still waiting for its turn in the bounded validation batch (50 per tick) |

If the page cannot load status at all, check that `status/status.json` is
public-read and CORS-open — the fetch is cross-origin — and that `/config.js`
on the viewer points at it (the chart renders it from `publicResultsBase` /
`viewer.statusBase`).

## Rebuilding the viewer image

The SPA is baked into the viewer image next to UV4, so any UI change needs a
rebuild:

```bash
make viewer-image                # local: bun build + docker build, tags 127.0.0.1:30500/uv4:dev
docker push 127.0.0.1:30500/uv4:dev   # prints the digest to pin as viewer.image
dagger call build-viewer         # reproducible: clones + patches UV, bun-builds the SPA
```

The image runs unprivileged and listens on **8080** (the chart's
`containerPort` and Service `targetPort` follow; NodePort 30800 is unchanged).

!!! info "`viewer.defaultManifest` is deprecated"

    It predates the campaign browser: when set, `/` 302-redirects to a single
    manifest in UV instead of serving the browser. Leave it empty unless you
    deliberately want the old single-volume front door.
