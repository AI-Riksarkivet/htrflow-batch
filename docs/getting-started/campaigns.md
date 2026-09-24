# Run a campaign

From an installed chart ([Deploy](deploy.md)) to volumes transcribing while
you watch. A campaign is a file in a git repository of its own, the
campaigns repo; the converter, `htrflow-campaigns`, checks it and applies it
to the cluster. [Campaigns](../how-it-works/campaigns.md) explains what
happens behind each step.

You need `uv` and a kubeconfig for the cluster.

## 1. Create the campaigns repo

```bash
uv tool install "git+https://github.com/AI-Riksarkivet/htrflow-batch@<release-tag>#subdirectory=packages/converter"
htrflow-campaigns init my-campaigns
```

`<release-tag>` is the release the chart came from. This installs the CLI
once; `uvx --from` with the same URL runs it without installing. With a
checkout of this repository, `uv tool install ./packages/converter` does
the same.

`my-campaigns/` has the shape of
[`examples/campaigns/`](https://github.com/AI-Riksarkivet/htrflow-batch/tree/main/examples/campaigns):

```
converter.yaml                 # where campaigns run: namespace, queue, S3 secret, results base
campaigns/demo.yaml            # a campaign: a pipeline and a list of volumes
pipelines/demo-v1.yaml         # a pipeline: the wrapper image by digest, and htrflow's steps
.github/workflows/render.yml   # CI: validate and policy-check on PR, render on main
argocd/apply.yaml              # the Argo CD hook that applies rendered/
```

Make it a git repository of its own and push it. Write access to it
decides which image and which models run with the bucket's write
credentials, so guard it like that
([Security → Trust boundary](../how-it-works/security.md#trust-boundary)).
`--ci azure` writes `azure-pipelines.yml` in place of `.github/`.

## 2. Point it at your install

In `converter.yaml`, set `public_results_base` to the chart's
`publicResultsBase`. `namespace`, `queue`, `s3_secret` and `data_pvc` must
name what the chart created; the defaults match the chart's defaults
([Deploy → Check it](deploy.md#5-check-it)).

## 3. List the volumes

```yaml title="campaigns/demo.yaml"
pipeline: demo-v1
volumes:
  - id: <volume-id>
    manifest: <iiif-manifest-url>
  - id: <another-volume-id>
    images:
      - <image-url>
```

A volume is a IIIF manifest or a list of image URLs. A bare string such as
`- R0001203` is a reference code, turned into a manifest URL by
`source_template` in `converter.yaml`; set that first, or `validate` refuses
it. `window:` caps how many volumes run at once. Every key is in
[Campaign & Pipeline YAML](../reference/campaign-yaml.md).

`pipelines/demo-v1.yaml` runs the published wrapper image with the demo
models. Under `values-prod.yaml` every model also needs its Hugging Face
commit hash as `revision:`, or admission refuses the campaign. The demo
pipeline has none: add one per model, where
[Campaign & Pipeline YAML](../reference/campaign-yaml.md) shows, before the
first apply.

## 4. Check and apply

```bash
htrflow-campaigns validate my-campaigns
htrflow-campaigns apply my-campaigns --out my-campaigns/rendered --namespace <namespace>
```

`validate` prints nothing when the repo is sound, and one line per problem
when it is not. `apply` renders the repo into `rendered/` and prints one
`applied: <Kind>/<name>` line per object. Exit `0` means everything was
applied, `3` that some objects were refused (each named on stderr), `1`
that nothing was applied or a pause was not enforced. `--dry-run` shows the list without touching the
cluster.

The warm-up Job downloads the pipeline's models into the model cache first,
once per pipeline. Kueue then admits the campaign when its GPUs are free,
and each campaign pod processes one volume.

## 5. Watch it

The campaign browser is the web front's root, `<web-front-url>/`: one card
per campaign, a bar per volume, and a sentence for each volume that failed.
From the cluster:

```bash
kubectl -n <namespace> get job <campaign> \
  -o jsonpath='{.status.completedIndexes} done / {.status.failedIndexes} failed{"\n"}'
```

A campaign that stays queued, or a volume that fails, is in
[Troubleshooting](troubleshooting.md).

## 6. Open the results

A volume's **open** link opens the viewer on its `iiif.json`, with the text
over the page image, before the volume has finished too. **log**
opens the run log, and each page's ALTO XML is one click from there
([View results](viewing.md)). In the bucket, results are under
`<namespace>/<pipeline>/<volume>/` ([S3 layout](../reference/s3-layout.md)).

## Changing work

Every change is a commit, then an apply.

- **New volumes go in a new campaign file.** A rendered campaign's volume
  list cannot change.
- **Pause** with `suspend: true` in the campaign file; remove it to resume.
- **Cancel** by deleting the campaign file and applying with `--prune`.
  Pruning deletes every converter-labelled object not in this render, so
  only run it on a full checkout.
- **A new image or new steps** is a new pipeline id and a new campaign
  file, never an edit to a pipeline that has results
  ([Campaign & Pipeline YAML](../reference/campaign-yaml.md)).

Results already in the bucket are never touched by any of these.

## Apply from CI

The generated CI validates, renders and policy-checks every pull request
with the chart's Kyverno policies, and commits `rendered/` on `main`. Set
its `CONVERTER_REF`, `POLICY_NAMESPACE`, `POLICY_ALLOWED_IMAGE_REPOS` and
`POLICY_REQUIRE_MODEL_REVISION` to match your release.

Something must then run `htrflow-campaigns apply --prune` on `main`: you,
from a kubeconfig, or the Argo CD `PostSync` hook in `argocd/apply.yaml`,
which runs it in the cluster (set `apply.rbac.enabled` and `apply.gitCidrs`
in the chart). Never point a GitOps sync at `rendered/` itself: it would
re-create a finished campaign's Job once Kubernetes has reaped it, and
rerun every volume. The details are in
[htrflow-campaigns CLI](../reference/cli.md).

## Your own pipeline image

Pin it by digest, never by tag, in `pipelines/<id>.yaml`:

```bash
docker inspect --format '{{index .RepoDigests 0}}' <registry>/htrflow-batch:<tag>
```

and add `<registry>/` to the chart's `security.allowedImageRepos`. Kyverno
checks both at admission and in CI; `validate` checks neither.
