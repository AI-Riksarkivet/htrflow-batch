# Run a campaign

This page takes you from an installed chart to a campaign that transcribes
itself while you watch. [Campaigns](../how-it-works/campaigns.md) explains
the mechanics behind each step.

Before you start, you need:

- the chart deployed with its S3 Secret ([Deploy](deploy.md));
- a `publicResultsBase` that browsers can reach;
- `kubectl` access to the cluster;
- `uv` wherever the converter runs, which is your machine or the campaigns
  repo's CI.

## 1. Create the campaigns repo

Desired state lives in a git repository of its own. The converter is a plain
Python package, not a container image, and it never runs as part of the
platform. Run it as a `uvx` tool straight from this repository to create a
campaigns repo:

```bash
uvx --from "git+https://github.com/AI-Riksarkivet/htrflow-batch@<ref>#subdirectory=packages/converter" \
  htrflow-campaigns init my-campaigns
```

`<ref>` is a release tag, a commit SHA or a branch. If `uvx --from` does not
resolve a subdirectory URL on your machine, install the CLI once instead:

```bash
git clone https://github.com/AI-Riksarkivet/htrflow-batch
uv tool install ./htrflow-batch/packages/converter
htrflow-campaigns init my-campaigns
```

Either way, `my-campaigns/` has the shape of
[`examples/campaigns/`](https://github.com/AI-Riksarkivet/htrflow-batch/tree/main/examples/campaigns):

```
converter.yaml                 # namespace, queue, window, S3 secret, PVC, runtime class, results base
campaigns/<campaign>.yaml      # pipeline: <id> + volumes:
pipelines/<id>.yaml            # image: <digest> + steps:
.github/workflows/render.yml   # validate and policy-check on PR; render + commit rendered/ on main
README.md                      # the repo's two rules: append-only campaigns, pause and cancel as Git changes
```

Set `converter.yaml` to agree with the chart: `namespace`, `queue`,
`s3_secret`, `data_pvc` and `public_results_base`
([Deploy](deploy.md#install)). All the file formats are in
[Campaign & Pipeline YAML](../reference/campaign-yaml.md).

Write access to this repository decides which image and which models run
with the bucket's write credentials. Treat it that way
([Security → Trust boundary](../how-it-works/security.md#trust-boundary)).

## 2. Pin an image digest

Every pipeline file names the wrapper image by digest, and the converter
rejects tags. The published images are on Docker Hub. For an image you
pushed yourself, read the digest back:

```bash
docker inspect --format '{{index .RepoDigests 0}}' <registry>/htrflow-batch:<tag>
```

Then put it in `pipelines/<id>.yaml`:

```yaml title="pipelines/<id>.yaml"
image: <registry>/htrflow-batch@sha256:<digest>
steps:
  - step: Segmentation
    ...
```

Two Kyverno rules check pipelines. Both run at admission, and again in the
campaigns repo's CI through the Kyverno CLI. `htrflow-campaigns validate`
does not check either of them.

- `<registry>/` must be one of the prefixes in the release's
  `security.allowedImageRepos`.
- With `security.requireModelRevision` on, every model needs a 40-character
  commit hash as `revision:`. It goes under `model_settings` for YOLO, or
  under `model_settings.model_kwargs` for TrOCR and other Hugging Face
  models.

A pipeline id names the image and the steps together. Once results exist
under an id, never change it in place. A new digest means a **new pipeline
id and a new campaign file**, not an edit
([Campaign & Pipeline YAML](../reference/campaign-yaml.md)).

## 3. Render and apply

### Through CI and GitOps

This is the normal path. `.github/workflows/render.yml` in the campaigns
repo does the following:

- On a pull request, it runs `htrflow-campaigns validate`, renders the repo,
  and runs the chart's Kyverno policies over the result with the Kyverno
  CLI.
- On every push to `main`, it renders and commits `rendered/`.

Set the workflow's `CONVERTER_REF`, `POLICY_NAMESPACE`,
`POLICY_ALLOWED_IMAGE_REPOS` and `POLICY_REQUIRE_MODEL_REVISION` to match
your release.

To apply what CI committed, point a GitOps tool at `rendered/`. For Argo CD,
that is an `Application` whose source is `rendered/`, with
`syncPolicy.automated.prune: true` so that deleting a campaign file cancels
the campaign. Nothing reaches the cluster except what CI committed.

When `htrflow-campaigns apply` itself runs inside the cluster (a CI Job, or
an Argo CD `PostSync` hook), it needs an identity that may write campaign
Jobs. Set `apply.rbac.enabled` in the chart to create one.

### From a kubeconfig

To render and apply directly:

```bash
make campaigns-apply DIR=<campaigns-repo-dir>
```

This is exactly:

```bash
uv run htrflow-campaigns apply <campaigns-repo-dir> --out <campaigns-repo-dir>/rendered
```

This one command does four things, in order:

1. Renders the repo.
2. Applies `rendered/pipelines`. Pipelines go first because a campaign's Job
   references its pipeline's ConfigMap.
3. Applies `rendered/campaigns`.
4. Sets each campaign's `suspend:` on its Kueue Workload.

It talks to the API server through the official Kubernetes client, with no
`kubectl` in the loop, and prints one `applied: <Kind>/<name>` line per
object. `--dry-run` prints the same list as `would apply: …` and opens no
connection at all.

One object the API server will not take does not stop the others: it is
named on stderr, the rest are applied, and the command exits **3** with a
summary line naming what was left unchanged. The codes are a precedence,
highest first: `1` is a pause that is not enforced — whatever else was
applied — or nothing applied at all, `3` is some objects refused with every
pause holding, and `0` is everything applied. See
[refused objects](../reference/campaign-yaml.md#when-the-api-server-refuses-an-object)
for what `apply` does about a Job whose pod template changed.

## 4. Add work: it is a commit

```yaml title="campaigns/<campaign>.yaml"
pipeline: <id>
volumes:
  - <reference>                    # expanded through converter.yaml's source_template
  - id: <volume-id>
    manifest: <iiif-manifest-url>
```

Open a pull request, where CI validates and policy-checks it. Get it
reviewed, then merge. Once `rendered/` is applied, Kueue admits the
campaign's Indexed Job up to its `window`, and Kubernetes runs one pod per
volume. There is no separate "enable" step and nothing to poll.

- **Campaigns are append-only.** An already-rendered campaign's volume list
  cannot be edited in place. New work goes in a new campaign file.
- **Pausing is a Git change.** Put `suspend: true` in the campaign file, and
  the apply step puts the same intent on the Kueue Workload.
- **Deleting a campaign's file cancels it.** Its Job and ConfigMap are
  removed by an apply that is asked to prune: Argo CD with
  `syncPolicy.automated.prune: true`, or `make campaigns-apply PRUNE=1`.
  Pruning deletes every converter-labelled object that is not in this apply,
  so never run it against a partial checkout.

Results already in S3 are never touched by any of these.

## 5. Watch it

The campaign browser is the platform's front door, at the web front's root:

```
<web-front-url>/
```

- A volume's **open** link goes to the Universal Viewer at `/uv.html`. Once
  the wrapper has published the volume's `iiif.json`, it opens that, with the
  text overlay. Before then it opens the source manifest, which shows images
  only.
- **source** links the volume's source manifest as the campaign file gives
  it. An `images:` volume has no source manifest, so it has no **source**
  link.
- **log** opens the run viewer at `/log`. It follows a running volume's log
  live, and shows the per-page summary once the volume finishes.

The browser gets progress from the read API (`GET /api/v1/jobs`). It holds no
cluster credentials of its own. A campaign's phase, counts and per-volume
state are read straight off the live Job every time the page asks.

The browser must reach both the web front and the results base URL. See
[Exposing the web front](viewing.md#exposing-the-web-front).

To skip the browser and ask the cluster directly:

```bash
kubectl -n <namespace> get job <campaign> \
  -o jsonpath='{.status.completedIndexes} done / {.status.failedIndexes} failed{"\n"}'
curl -s <web-front-url>/api/v1/jobs | jq .
```
