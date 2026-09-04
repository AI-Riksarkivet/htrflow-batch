# Running a Campaign

How to go from "the chart is installed" to "a campaign is transcribing itself
and I can watch it". The mechanics behind each step are in
[Campaigns (Indexed Jobs)](../how-it-works/campaigns.md).

Prerequisites: the chart deployed ([Deploy](deploy.md)), the S3 secret in
place, a browser-reachable `publicResultsBase`, `kubectl` access to the
cluster, and `uv` on your machine (or CI runner) to run the converter.

## 0. Create the campaigns repo

Desired state lives in its own git repo. The converter is a plain Python
package, not a container image — it never runs in the cluster; run it as a
`uvx` tool straight from this repo to create a new campaigns repo in one
command:

```bash
uvx --from "git+https://github.com/AI-Riksarkivet/htrflow-batch@<tag>#subdirectory=packages/converter" \
  htrflow-campaigns init my-campaigns
```

(`<tag>`: a released htrflow-batch tag, or a commit SHA; `@main` works too
while no release exists yet.) If `uvx --from` with a subdirectory URL does
not resolve on your host, install the CLI once instead:

```bash
git clone https://github.com/AI-Riksarkivet/htrflow-batch
uv tool install ./htrflow-batch/packages/converter
htrflow-campaigns init my-campaigns
```

Either way, `my-campaigns/` comes out in this shape — see it live at
[`examples/campaigns/`](https://github.com/AI-Riksarkivet/htrflow-batch/tree/main/examples/campaigns),
which is exactly what the command above writes:

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
    immediately.** Then set the htrflow-batch release's
    `security.allowedImageRepos` (with `security.policies.enabled`) so only
    images from your registry can start in the namespace at all
    ([Security → Trust boundary](../development/security.md#trust-boundary)).

## 1. Pin an image digest

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

`<registry>/` must be one of the prefixes in the release's
`security.allowedImageRepos`, and with `security.requireModelRevision`
every model needs a 40-hex `revision:` — both enforced by Kyverno at
admission, and by the Kyverno CLI in the campaigns repo's CI, not by
`htrflow-campaigns validate`.

!!! warning "User action: publish to a real registry before any non-PoC campaign"

    The PoC pins digests in the in-cluster registry (`127.0.0.1:30500/…`),
    which only resolves from the PoC node. Anything beyond the PoC needs the
    image pushed to a real registry (GHCR, Harbor) and re-pinned. Treat an
    existing pipeline id as immutable once results exist under it: re-pinning
    means a **new pipeline id and a new campaign file**, not an edit
    ([Immutability](../reference/campaign-yaml.md#immutability)).

## 2. Render and apply

On the PoC, render and apply directly with `make campaigns-apply`:

```bash
make campaigns-apply DIR=/path/to/your-campaigns-repo
```

which is exactly:

```bash
uv run htrflow-campaigns apply $(DIR) --out $(DIR)/rendered
```

— one command that renders, applies `rendered/pipelines` and then
`rendered/campaigns` (pipelines first: a campaign's Job references its
pipeline's ConfigMap), and finally puts each campaign's `suspend:` on its
Kueue Workload. Add `--dry-run` to see the `kubectl` commands without running
them; every command it does run is echoed to stderr.
Beyond the PoC, `examples/campaigns/.github/workflows/render.yml` renders and
commits `rendered/` on every push to `main`, and Argo CD's Application points
its source at `rendered/` in that repo — nothing applies to the cluster
outside of what CI committed.

## 3. Add work — it is a commit

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

**Pausing a campaign is a Git change** — `suspend: true` on the campaign
file; the apply step puts the same intent on the Kueue Workload (see
[Pausing](../reference/campaign-yaml.md#pausing)). **Deleting a campaign's
file cancels it** — its Job and ConfigMap are pruned by an apply that is
*asked* to prune (Argo CD with `syncPolicy.automated.prune: true`, or
`make campaigns-apply PRUNE=1`; `kubectl delete -f` by hand on the PoC).
**Results already in S3 are never touched by anything here.**

## 4. Watch it

The campaign browser is the platform's front door:

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
    [Viewing Results](viewing.md#reaching-the-web-front-over-ssh-poc-bare-k3s).

Or skip the browser and ask the cluster directly:

```bash
kubectl -n htr-batch get job trolldomskommissionen \
  -o jsonpath='{.status.completedIndexes} done / {.status.failedIndexes} failed{"\n"}'
curl -s http://<node>:30800/api/v1/jobs | jq .
```

## Rebuilding the web image

The SPA and UV4 are baked into the web image next to the read API, so any UI
change needs a rebuild:

```bash
make build-web                   # local: bun-builds the SPA, clones + patches UV, tags 127.0.0.1:30500/htrflow-web:dev
docker push 127.0.0.1:30500/htrflow-web:dev   # prints the digest to pin as web.image
dagger call build-web            # the same dockerfile, through the dagger engine
```

The image runs unprivileged and listens on **8081** (the chart's
`containerPort` and Service `targetPort` follow; NodePort 30800 is
unchanged).
