# Campaign & Pipeline YAML

The campaigns git repo has three files/directories the converter reads:
`converter.yaml` (cluster-wide defaults), `campaigns/*.yaml` (what to run)
and `pipelines/*.yaml` (how to run it). The filename stem is the campaign /
pipeline id. `htrflow-campaigns init <dir>` writes a repo in exactly this
shape — see [Running a Campaign → Create the campaigns
repo](../getting-started/campaigns.md#0-create-the-campaigns-repo).

Source: [`packages/converter/src/htrflow_converter/parse.py`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/packages/converter/src/htrflow_converter/parse.py),
[`models.py`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/packages/converter/src/htrflow_converter/models.py).

## `converter.yaml`

Cluster-wide defaults for everything the converter renders. Unknown keys are
rejected. All fields are optional — the values below are the defaults.

```yaml title="converter.yaml"
namespace: htr-batch              # Kubernetes namespace campaigns render into
queue: htr-batch                  # Kueue LocalQueue name
window: 20                        # Job parallelism, and the CAP a campaign's own `window:` is clamped to
s3_secret: htr-batch-s3           # Secret carrying S3 credentials
data_pvc: htr-test-data           # PVC mounted as the model cache
runtime_class: nvidia             # RuntimeClass for GPU pods
node_selector: {}
tolerations: []
public_results_base: ""           # public URL prefix results are served from (required for the read API)
source_template: "https://lbiiif.riksarkivet.se/arkis!{ref}/manifest"
max_seconds: 21600                # each pod's activeDeadlineSeconds; a pipeline's own `max_seconds:` overrides it
manifest_max_bytes: 16777216      # 16 MiB
fetch_max_bytes: 67108864         # 64 MiB
allowed_image_repos: []           # empty = any registry; see Security → Trust boundary
require_model_revision: false
```

## Campaign file — `campaigns/<name>.yaml`

```yaml
pipeline: demo-v1          # required: a pipeline id from pipelines/
priority: ""                # optional: a Kueue PriorityClass name
window: 20                   # optional: this campaign's parallelism, clamped to converter.yaml's window
suspend: false               # optional: true pauses this campaign (see "Pausing" below)
volumes:
  # 1) Bare string: a Riksarkivet reference code. The manifest URL is
  #    templated from converter.yaml's source_template.
  - R0001203

  # 2) Explicit IIIF manifest (Presentation v2 or v3), http(s) only:
  - id: loc-mal2459400
    manifest: https://www.loc.gov/item/mal2459400/manifest.json

  # 3) Bare image URLs (http(s) only) — the wrapper builds and publishes
  #    a synthetic IIIF manifest for them itself (see S3 Layout: sources/):
  - id: htr-demo-examples
    images:
      - https://example.org/page1.jpg
      - https://example.org/page2.jpg
```

Rules enforced by `parse_campaign` (`validate`, and by `render`):

| Rule | Consequence when violated |
|------|---------------------------|
| `pipeline:` is required and must name a file in `pipelines/` | Reported as a validation error; nothing renders |
| Every volume needs `manifest:` or a non-empty `images:` (unless it is a bare string) | Validation error |
| `manifest:` and every `images:` entry are absolute `http://` or `https://` URLs | Validation error (`must be an http(s) URL`) |
| Volume ids match `[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?` — alphanumeric at both ends, ≤63 chars | Validation error (`unsafe volume id`). This is the Kubernetes **label-value** alphabet, not a DNS-1123 label: uppercase is allowed |
| Volume ids are unique within a campaign | Validation error (`duplicate volume id`) |
| `window:`, when set, is a positive integer | Validation error |
| `window:` above `converter.yaml`'s `window` | Silently clamped to it at render time — `converter.yaml`'s value is the per-cluster cap and should be set to what the ClusterQueue's GPU quota can actually admit. Rendering more and letting Kueue's partial admission shrink it on the live Job is what this replaced: Kueue then rewrites `spec.parallelism` and rejects every later apply of the unchanged rendered file (`cannot change when partial admission is enabled and the job is not suspended`) |
| `suspend: true` | Renders `spec.suspend: true` — see [Pausing](#pausing) |
| **A campaign whose rendered Job already exists in `rendered/` with a different volume list is rejected** | `render` prints `campaign <name> is append-only: create a new campaign` and exits non-zero — Job `completions` is immutable once created, so adding volumes means a new campaign file |

The campaign file stem becomes the Job name and the `htrflow.riksarkivet.se/campaign`
label.

## Pipeline file — `pipelines/<id>.yaml`

```yaml
# Image MUST be digest-pinned — a mutable tag would let results published
# under one id be produced by different code over time.
image: ghcr.io/riksarkivet/htrflow-batch@sha256:34d462b8de7d…

max_seconds: 3600          # optional: this recipe's per-volume wall-clock
                           # budget (the pod's activeDeadlineSeconds),
                           # overriding converter.yaml's

steps:                     # htrflow pipeline steps, passed through verbatim
  - step: Segmentation
    settings:
      model: yolo
      model_settings:
        model: Riksarkivet/yolov9-regions-1
        revision: 0123456789abcdef0123456789abcdef01234567   # required when
                                                             # require_model_revision
  - step: TextRecognition
    settings:
      model: TrOCR
      model_settings:
        model: Riksarkivet/trocr-base-handwritten-hist-swe-2
        revision: …
```

Rules enforced by `parse_pipeline` — a broken pipeline is reported as a
validation error and blocks rendering for every campaign that uses it:

| Rule | Why |
|------|-----|
| Pipeline id is a DNS-1123 label (lowercase, `[a-z0-9.-]` interior, ≤63 chars) | It becomes the ConfigMap name `htr-pipeline-<id>` |
| `image:` contains `@sha256:` | Digest pin — provenance is recorded per volume in `manifest.json` |
| When `converter.yaml`'s `allowed_image_repos` is set: the repository (the part before `@`) equals one entry or starts with `<entry>/` | The campaigns repo is a code-execution boundary; the allow-list is what keeps it to images you built |
| When `converter.yaml`'s `require_model_revision` is true: every `model_settings.model` carries a 40-hex `revision:` | HF Hub weights are pickles and an unpinned repo is mutable |
| `max_seconds:`, when set, is a positive integer | It becomes `spec.template.spec.activeDeadlineSeconds` — the *pod's* deadline, so only the overrunning attempt is killed — for every campaign on this pipeline; unset falls back to `converter.yaml`. A sixty-page spread recipe and a single-page one do not want the same budget, and a budget the volume cannot meet costs `backoffLimitPerIndex` retries before the index is capped |
| `steps:` is present | Only the `steps:` document goes into the ConfigMap; no `Export` steps (the wrapper appends them — a pipeline with one fails the warm-up) |

## When something is wrong

`validate` and `render` print one line per problem and then a count, and
render nothing at all if there is one problem — a half-rendered `rendered/`
would be worse than none. Every line is
`path/to/file.yaml: <what is wrong> — <what to write instead>`; there are no
Python tracebacks, no `volumes.0.id` paths and no pydantic phrasing in them,
because the person reading them is looking at YAML, not at a parser.

```
campaigns/broken.yaml: volume 1 ("a/b") has an id with characters that are not allowed — use only letters, digits, ".", "_" and "-", at most 63 of them
campaigns/broken.yaml: volume "R1" is listed twice — remove the duplicate
2 problems in 1 file — nothing was rendered
```

| What you wrote | What you are told |
| --- | --- |
| An `id:` with `/`, a space or 64+ characters | `volume 3 ("a/b") has an id with characters that are not allowed — use only letters, digits, ".", "_" and "-", at most 63 of them` |
| The same id twice in one campaign | `volume "R1" is listed twice — remove the duplicate` |
| A volume with neither `manifest:` nor `images:` (or with both) | `volume 3 needs exactly one source — give it manifest: <IIIF manifest URL>, or images: <list of image URLs>` |
| A `manifest:` that is not an http(s) URL | `volume 3 has a manifest that is not an http(s) URL ("javascript:alert(1)") — write the whole URL, starting with https://` |
| A list entry that is neither a bare id nor a mapping with `id:` | `volume 3 has no id — write the entry as "- R1", or as "- id: R1" with manifest: or images:` |
| `pipeline:` naming a file that is not in `pipelines/` | `pipeline "kyrk-v3" has no file in pipelines/ — add pipelines/kyrk-v3.yaml, or point pipeline: at one that is there` |
| A tag instead of a digest in `image:` | `"image" is not pinned to a digest (got "repo/img:v5") — write image: <registry>/<repo>@sha256:<64 hex digits>` |
| An image outside `allowed_image_repos` | `the image is not from an allowed repository ("ghcr.io/other/x@sha256:…") — converter.yaml allows only: ghcr.io/riksarkivet` |
| A model with no `revision:` while `require_model_revision` is on | `the model "Riksarkivet/yolov9-regions-1" is not pinned to a revision — converter.yaml sets require_model_revision, so add revision: <40-character commit hash>` |
| `steps:` that is not a list | `"steps" must be a list of steps — write steps: and then "- step: <Name>" entries under it` |
| `window: "5"` (quoted, so YAML makes it text), `window: 0`, `window: true` | `"window" must be a whole number of 1 or more (got "5" — quotes make it text)` — the "quotes" half is added only when the value really is a number, so `suspend: maybe` is not told about quotes it does not have |
| `max_seconds:` likewise | `"max_seconds" must be a whole number of seconds, 1 or more (got 0)` |
| `suspend: maybe` (or any other non-boolean) | `"suspend" must be true or false (got "maybe")` |
| A setting given a list or a block where one value belongs | `"window" must be a whole number (got a list)` — the value is described, never dumped as a Python repr |
| A bad value inside a nested setting | `"node_selector.a" must be text (got 1)`, `"tolerations" entry 1 must be settings written as "key: value" lines (got 3)` — a list position is counted from 1, never shown as `tolerations.0` |
| A key `converter.yaml` does not have | `"bogus_field" is not a setting this file has — remove it, or fix the spelling` |
| A required key left out | `"image" is missing — add "image:" to this file` |
| Broken indentation or quoting | `this file is not valid YAML — <the line and column PyYAML names>` |
| A file that is a list, or free text | `this file must be campaign settings written as "key: value" lines — a bare list or a piece of text is not one` |

A campaign whose pipeline file is itself broken is **not** also told its
pipeline is missing: that file's own problem is already in the list, and
saying it twice would send its author looking for a file that is right there.

The wording is pinned verbatim, per fixture, in
`packages/converter/tests/test_parse.py` (`EXPECTED`) — changing a sentence
is a deliberate edit there.

## `rendered/`

`htrflow-campaigns render <repo-dir> --out <repo-dir>/rendered` writes:

```
rendered/
  pipelines/<id>.yaml     # ConfigMap htr-pipeline-<id> + Job htr-warmup-<id>
  campaigns/<name>.yaml   # ConfigMap campaign-<name> + the campaign's Indexed Job
```

This directory is generated, committed by the campaigns repo's own CI on
`main` (never hand-edited), and is what Argo CD (or `make campaigns-apply`
on the PoC) actually applies — pipelines first, since a campaign's Job
references its pipeline's ConfigMap. `htrflow-campaigns apply` does that
ordering itself; by hand it is `kubectl apply -f rendered/pipelines -f
rendered/campaigns`.

`render` also **removes** files under `--out` that this render did not
produce, so deleting `campaigns/<name>.yaml` deletes
`rendered/campaigns/<name>.yaml` too. Deleting the manifest is only half of
cancelling: the apply has to prune as well, and **pruning is opt-in on both
sides**. Argo CD's `syncPolicy.automated.prune` defaults to `false` and a
manual sync does not prune unless asked, so an Application that manages this
repo needs `syncPolicy.automated.prune: true` (or `argocd app sync --prune`);
by hand it is `make campaigns-apply PRUNE=1`, which runs
`htrflow-campaigns apply --prune` — that lists every Job and ConfigMap in
the namespace carrying `htrflow.riksarkivet.se/managed-by=converter` and
deletes the ones this render did not produce. Every object the converter
renders — both ConfigMaps and both Jobs — carries that label for exactly
this reason.

The four objects above are not built up field-by-field in Python: the
skeletons **are** the Job/ConfigMap, checked in as real YAML at
[`packages/converter/src/htrflow_converter/manifests/`](https://github.com/AI-Riksarkivet/htrflow-batch/tree/main/packages/converter/src/htrflow_converter/manifests)
(`campaign-job.yaml`, `warmup-job.yaml`, `configmap.yaml`,
`pipeline-configmap.yaml`) with placeholder values (`name: CAMPAIGN`,
`image: IMAGE`, …) for the fields `render.py` fills in at render time — read
them there for the exact static shape of what gets applied.

## Pausing

`suspend: true` on a campaign renders `spec.suspend: true` on its Job — but
**Kueue owns `spec.suspend` for a Workload it has admitted** and flips it back
within seconds. The rendered field is the declared intent; enforcement happens
at apply time, on the Workload:

```bash
htrflow-campaigns apply <campaigns-repo>      # = make campaigns-apply DIR=…
```

The command talks to the API server directly (the official Kubernetes
client, server-side apply — there is no `kubectl` to install), and its last
step patches `spec.active` on each campaign's Workload (`false` for a suspended campaign, `true` otherwise), idempotently.
Deactivating a Workload evicts its pods, keeps every finished index, and
`kubectl get job` reports `suspend: true`; reactivating continues at the next
index. Results already in S3 are never touched.

**A brand-new paused campaign has no Workload at the instant the apply
returns** — Kueue creates it a moment later, and that moment is exactly the
window in which Kueue would admit and start the campaign. Skipping it would
therefore *run* a campaign that git says is paused, so the apply waits
(`--pause-wait`, 10 × 1 s by default) for a paused campaign's Workload and
exits non-zero with a message if it never appears: re-run the apply. A
campaign that is *not* paused is still skipped when its Workload is missing —
a Workload that does not exist is not admitted either, and the next apply
catches it.

!!! warning "A campaign committed as `suspend: true` still runs one pod for ~4 s"

    Kueue creates, admits and unsuspends the Job in the same second it is
    created — before any apply-time step can look at it — so one pod starts
    and is deleted a few seconds later, mid-volume. Nothing is written for
    that volume (the index is simply retried when the campaign resumes) but
    it is not "no pod ever starts".

    The race-free alternative — rendering a paused campaign's Job **without**
    the `kueue.x-k8s.io/queue-name` label, so Kueue never sees it — was
    measured on the PoC and rejected: it strands an admitted Workload that
    goes on holding the campaign's quota, starving every other campaign in
    the ClusterQueue, and Kueue's webhook then refuses to put the label back
    (`metadata.labels[kueue.x-k8s.io/queue-name]: field is immutable`) on the
    resuming apply. The measurements are in
    [the E2E run log](../development/e2e-indexed-jobs.md) under *Task 14*.

**The apply also writes `active: true` for every campaign that is not
suspended.** So a Workload that Kueue deactivated on its own — a requeue
limit hit, `maximumExecutionTimeSeconds` exceeded — is re-admitted at the next
apply and the campaign resumes at the next unfinished index. Git is the truth
about what should be running; nothing on the cluster stays paused unless the
campaign file says so.

With Argo CD, run the same command as a `PostSync` hook so a merged
`suspend: true` takes effect on sync. **The manifest below is illustrative and
has not been run** — there is no Argo CD on the PoC (the E2E drives the
command through `make campaigns-apply` instead), and the image digest and
the checkout are placeholders you have to fill in. No new image is needed:
`uvx` installs the converter from this repo at the pinned ref, and the pod
needs a checkout of the campaigns repo to render (the hook re-renders rather
than trusting the synced `rendered/`):

```yaml title="argocd/pause-sync-hook.yaml"
apiVersion: batch/v1
kind: Job
metadata:
  name: htrflow-campaigns-apply
  annotations:
    argocd.argoproj.io/hook: PostSync
    argocd.argoproj.io/hook-delete-policy: HookSucceeded
spec:
  template:
    spec:
      restartPolicy: Never
      serviceAccountName: htrflow-campaigns        # helm ... --set
                                                   # apply.rbac.enabled=true
      containers:
        - name: apply
          image: ghcr.io/astral-sh/uv@sha256:…     # any pinned uv image
          command: ["uvx", "--from",
                    "git+https://github.com/AI-Riksarkivet/htrflow-batch@<ref>#subdirectory=packages/converter",
                    "htrflow-campaigns", "apply", "/repo"]
          volumeMounts: [{name: repo, mountPath: /repo}]
      volumes: [ … ]                               # the campaigns repo checkout
```

The ServiceAccount is what the htrflow-batch chart renders behind
`apply.rbac.enabled=true` (default `false`): a Role — never a ClusterRole —
with `list`/`create`/`patch`/`delete` on `jobs` and `configmaps` and
`list`/`patch` on `workloads.kueue.x-k8s.io`, in the release namespace
only. `create` is not redundant next to `patch`: a server-side apply whose
object does not exist yet is authorized as both. Nothing else has to be on
the image — the converter carries its own Kubernetes client, so there is no
`kubectl` to install. Add `--prune` to make a deleted campaign file cancel
its campaign; leave it off and Argo CD's own prune does the same job.

## Immutability

A pipeline id is a **permanent name for a recipe**: changing the steps or the
image under an existing id is drift once results exist under it. There is no
runtime drift guard any more — the converter is a pure function and renders
whatever is in git — so this is enforced by review convention, not code:
protect the campaigns repo's `main` and require review, the same as for CI
config. To change a recipe, mint a new id (`demo-v2`); old results under
`demo-v1` stay untouched and comparable side by side.

A campaign, separately, is append-only at the volume-list level (see the
table above) — that one *is* enforced by `render`, because a running Job's
`completions` genuinely cannot change.
