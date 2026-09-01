# Running a Campaign

How to go from "the chart is installed" to "a campaign is transcribing itself
and I can watch it". The mechanics behind each step are in
[Campaigns (Indexed Jobs)](../how-it-works/campaigns.md).

Prerequisites: the chart deployed ([Deploy](deploy.md)), the S3 secret in
place, a browser-reachable `publicResultsBase`, `kubectl` access to the
cluster, and the [`htrflow-campaigns` CLI](#0-install-the-converter-cli)
available (locally, or through CI in your campaigns repo).

## 0. Install the converter CLI

The converter is a plain Python package, not a container image — it never
runs in the cluster. Run it as a `uvx` tool straight from this repo:

```bash
uvx --from "git+https://github.com/AI-Riksarkivet/htrflow-batch#subdirectory=packages/converter" \
  htrflow-campaigns --help
```

or install it once with `uv tool install` from a checkout if `uvx --from`
with a subdirectory URL does not resolve on your host:

```bash
git clone https://github.com/AI-Riksarkivet/htrflow-batch
uv tool install ./htrflow-batch/packages/converter
```

## 1. Create the campaigns repo

Desired state lives in its own git repo. See
[`examples/campaigns/`](https://github.com/AI-Riksarkivet/htrflow-batch/tree/main/examples/campaigns)
for the exact shape to copy:

```
converter.yaml                 # namespace, queue, window, S3 secret, PVC, …
campaigns/<campaign>.yaml      # pipeline: <id> + volumes:
pipelines/<id>.yaml            # image: <digest> + steps:
.github/workflows/render.yml   # validate on PR; render + commit rendered/ on main
README.md                      # states the two GitOps rules in bold
```

The formats are in [Campaign & Pipeline YAML](../reference/campaign-yaml.md).

!!! danger "User action: protect `main` before anything applies from it"

    Write access to this repo is effectively **cluster operator**: a
    pipeline file names the image that runs on the GPU with the bucket's
    write credentials, and the Hugging Face model repos the warm-up pod
    loads. **Enable branch protection on `main` with required review,
    immediately.** Then set `converter.yaml`'s `allowed_image_repos` so
    only images from your registry can be named at all
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

With `allowed_image_repos` set, `<registry>/` must be one of the
listed prefixes; with `require_model_revision`, every model needs a
40-hex `revision:`.

!!! warning "User action: publish to a real registry before any non-PoC campaign"

    The PoC pins digests in the in-cluster registry (`127.0.0.1:30500/…`),
    which only resolves from the PoC node. Anything beyond the PoC needs the
    image pushed to a real registry (GHCR, Harbor) and re-pinned. Treat an
    existing pipeline id as immutable once results exist under it: re-pinning
    means a **new pipeline id and a new campaign file**, not an edit
    ([Immutability](../reference/campaign-yaml.md#immutability)).

## 3. Render and apply

On the PoC, render and apply directly with `make campaigns-apply`:

```bash
make campaigns-apply DIR=/path/to/your-campaigns-repo
```

which is exactly:

```bash
uv run htrflow-campaigns render $(DIR) --out $(DIR)/rendered
kubectl apply -f $(DIR)/rendered/pipelines -f $(DIR)/rendered/campaigns
```

(pipelines first — a campaign's Job references its pipeline's ConfigMap).
Beyond the PoC, `examples/campaigns/.github/workflows/render.yml` renders and
commits `rendered/` on every push to `main`, and Argo CD's Application points
its source at `rendered/` in that repo — nothing applies to the cluster
outside of what CI committed.

## 4. Add work — it is a commit

```yaml title="campaigns/trolldomskommissionen.yaml"
pipeline: demo-v1
volumes:
  - R0001203
  - id: dodsbok-1698
    manifest: https://iiif.example.org/xyz/manifest
```

Open a PR (CI runs `htrflow-campaigns validate`), get it reviewed, merge.
Once `rendered/` is applied, Kueue admits the campaign's Indexed Job up to
`window` and Kubernetes runs one pod per volume — no separate "enable" step,
nothing to poll.

Removing a volume from a campaign file only affects future campaign files —
per the append-only rule below, an already-rendered campaign's volume list
cannot be edited in place; results already in S3 are never touched.

**Pausing a campaign is a Git change** — `suspend: true` on the campaign's
rendered Job, applied the normal way. **Deleting a campaign's file cancels
it** — its Job and ConfigMap are pruned by Argo (or `kubectl delete -f`, on
the PoC). **Results already in S3 are never touched by anything here.**

## 5. Watch it

The campaign browser is the viewer's front door:

```
http://<node>:30800/
```

The same container serves the Universal Viewer at `/uv.html` — a volume's
`open` link goes there with the published `iiif.json` (text overlay) for a
done volume, `source` with the source manifest (images only) for everything
else — and the run viewer at `/log`, which follows a running volume's log
live and shows the per-page summary once it finishes. The browser talks to
the read API (`GET /api/v1/jobs`) for progress — no cluster credentials of
its own, nothing to poll for staleness: a campaign's `phase`, counts and
per-volume state are read straight off the live Job every time the page asks.

!!! note "Reaching it from a laptop"

    On the bare-k3s PoC both NodePorts must be tunnelled — the page comes from
    30800, results and logs from 30900. See
    [Viewing Results](viewing.md#reaching-the-viewer-over-ssh-poc-bare-k3s).

Or skip the browser and ask the cluster directly:

```bash
kubectl -n htr-batch get job trolldomskommissionen \
  -o jsonpath='{.status.completedIndexes} done / {.status.failedIndexes} failed{"\n"}'
curl -s http://<node>:30900/api/v1/jobs | jq .
```

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
