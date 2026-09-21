# Campaign & Pipeline YAML

A campaigns git repo has three things the converter reads:
`converter.yaml` (cluster-wide defaults), `campaigns/*.yaml` (what to run)
and `pipelines/*.yaml` (how to run it). The filename stem is the campaign /
pipeline id. `htrflow-campaigns init <dir>` writes a repo in exactly this
shape — see [Running a Campaign → Create the campaigns
repo](../getting-started/campaigns.md#1-create-the-campaigns-repo).

Source: [`packages/converter/src/htrflow_converter/parse.py`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/packages/converter/src/htrflow_converter/parse.py),
[`models.py`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/packages/converter/src/htrflow_converter/models.py).

## `converter.yaml`

Cluster-wide defaults for everything the converter renders. Unknown keys are
rejected. Every field is optional; the values below are the defaults, except
`source_template`, which is shown as a placeholder — set it to your IIIF
source whenever a campaign lists bare volume ids.

```yaml title="converter.yaml"
namespace: htr-batch              # Kubernetes namespace campaigns render into
queue: htr-batch                  # Kueue LocalQueue name
window: 20                        # Job parallelism, and the CAP a campaign's own `window:` is clamped to
priority_classes: [htr-interactive, htr-bulk, htr-idle]   # the WorkloadPriorityClass names the chart ships; what a campaign's `priority:` may name
s3_secret: htr-batch-s3           # Secret carrying S3 credentials
hf_token_secret: ""               # optional: Secret with a `token` key, read by the warm-up for a private or gated Hub model
data_pvc: htr-test-data           # PVC mounted as the model cache
runtime_class: nvidia             # RuntimeClass for GPU pods — on the warm-up Job too
node_selector: {}
tolerations: []
public_results_base: ""           # public URL prefix results are served from (required for the read API)
source_template: "https://<iiif-host>/<path>/{ref}/manifest"   # manifest URL for a bare volume id; {ref} is the id
max_seconds: 21600                # each pod's activeDeadlineSeconds; a pipeline's own `max_seconds:` overrides it
warmup_wait_seconds: 900          # how long a pod waits for its pipeline's warm-up marker before failing the index; capped by that pod's own deadline
ttl_seconds_after_finished: 604800  # a week: how long a finished campaign's Job stays before Kubernetes deletes it; a pipeline's own `ttl_seconds_after_finished:` overrides it
manifest_max_bytes: 16777216      # 16 MiB
fetch_max_bytes: 67108864         # 64 MiB
```

The file itself is required: every command refuses a repo without a
`converter.yaml` rather than falling back to defaults, because the namespace
a campaign is applied to — and pruned in — is one of the settings that would
be guessed at.

`queue`, `s3_secret`, `data_pvc` and `priority_classes` name objects the
htrflow-batch chart creates; the [Configuration](configuration.md) page
shows which chart value each must agree with. `priority_classes` is checked
here rather than by the cluster because Kueue does not refuse a Job naming a
class that does not exist: no Workload is created, no event is raised, and
the campaign reads "Queued" for ever. An empty list means the cluster offers
no priority and every `priority:` is refused.

`hf_token_secret` names an object no chart creates — you make it yourself,
like the S3 Secret. Leave it unset unless a pipeline pulls a **private or
gated** model from Hugging Face Hub: then create a Secret in the campaign
namespace with a single `token` key holding a Hub token with **read** scope,
and name it here. The converter renders it as `HF_TOKEN` into that
pipeline's warm-up Job and nowhere else. Campaign pods run
`HF_HUB_OFFLINE=1` against the cache the warm-up filled, and have no route
to the Hub, so they never need it
([The model cache](../how-it-works/wrapper.md#the-model-cache)).

!!! note "The image allow-list and the model-revision rule are cluster policy"

    They are Kyverno `ClusterPolicy` objects the htrflow-batch chart ships
    (`security.allowedImageRepos`, `security.requireModelRevision`, behind
    `security.policies.enabled`), not converter settings: a rule the
    converter applied would only ever see what the converter rendered, while
    admission sees everything the namespace admits. A `converter.yaml` that
    carries `allowed_image_repos` or `require_model_revision` is a
    validation error naming the chart value. A campaigns repo's CI runs the
    same policies over `rendered/` with the Kyverno CLI, so a pull request
    still fails early. See [Security](../how-it-works/security.md#trust-boundary).

## Campaign file — `campaigns/<name>.yaml`

```yaml
pipeline: demo-v1          # required: a pipeline id from pipelines/
priority: ""                # optional: one of converter.yaml's priority_classes (htr-interactive, htr-bulk, htr-idle);
                            # orders the queue, never evicts a running campaign; empty is htr-bulk
window: 20                   # optional: this campaign's parallelism, clamped to converter.yaml's window
suspend: false               # optional: true pauses this campaign (see "Pausing" below)
volumes:
  # 1) Bare string: a reference code at your IIIF source. The manifest URL
  #    is templated from converter.yaml's source_template.
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

!!! warning "Source URLs are not secrets"

    Every `manifest:` and `images:` URL is stored verbatim — in git, in the
    committed `rendered/`, in the campaign's ConfigMap and in each volume's
    `manifest.json`. A presigned URL therefore publishes its signature to
    everyone who can read any of those. Validation problems echo such a URL
    back with its userinfo and its signing query parameter blanked, because
    those lines travel further still, but the stored URL is untouched. See
    [Source URLs are not secrets](../how-it-works/security.md#source-urls-are-not-secrets).

Rules enforced by `parse_campaign` (`validate`, and by `render`):

| Rule | Consequence when violated |
|------|---------------------------|
| `pipeline:` is required and must name a file in `pipelines/` | Reported as a validation error; nothing renders |
| A campaign lists at least one volume | Validation error — no volumes renders a Job with `completions: 0`, which Kubernetes reports as Succeeded the moment it is created |
| Every volume needs `manifest:` or a non-empty `images:` (unless it is a bare string) | Validation error |
| `manifest:` and every `images:` entry are absolute `http://` or `https://` URLs | Validation error (`must be an http(s) URL`) |
| No whitespace (space, tab, line break) inside a `manifest:` or `images:` URL | Validation error naming the volume and the image — percent-encode a space as `%20`. Whitespace separates the URLs of an `images:` volume in `volumes.txt`, which is why it cannot appear inside one; a comma can (a IIIF size such as `/full/2500,/`) |
| An `images:` volume whose one line of `volumes.txt` is over 100 KiB | Validation error naming the volume and how many images it lists. The Job exports that line's URLs as a single `IMAGES` environment entry, and Linux stops one entry at 128 KiB — the pod would die with `Argument list too long` before the wrapper starts. Split the volume, or give it a IIIF manifest |
| Volume ids match `[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?` — alphanumeric at both ends, ≤63 chars | Validation error (`unsafe volume id`). This is the Kubernetes **label-value** alphabet, not a DNS-1123 label: uppercase is allowed |
| Volume ids are unique within a campaign | Validation error (`duplicate volume id`) |
| `priority:`, when set, is a class name (lower-case letters, digits, `-`, alphanumeric at both ends, ≤63) | Validation error naming the `kueue.x-k8s.io/priority-class` label it is rendered into — a case slip like `HTR-Bulk` is a legal label that names no class |
| `priority:` is one of `converter.yaml`'s `priority_classes` (`htr-interactive`, `htr-bulk`, `htr-idle` by default, mirroring the chart's `queue.priorityClasses`) | Validation error naming the file, the classes there are and the chart value. **Checked here because the cluster will not**: Kueue does not refuse a Job naming a class that does not exist — no Workload, no event, and the campaign reads "Queued" for ever. Leaving the field out is `htr-bulk`; a higher class is admitted before every waiting campaign but never evicts a running one ([Queueing](../how-it-works/queueing.md)) |
| `window:`, when set, is a positive integer | Validation error |
| `window:` above `converter.yaml`'s `window` | Silently clamped to it at render time — `converter.yaml`'s value is the per-cluster cap and should be set to what the ClusterQueue's GPU quota can actually admit. Rendering more would let Kueue's partial admission shrink it on the live Job: Kueue then rewrites `spec.parallelism` and rejects every later apply of the unchanged rendered file (`cannot change when partial admission is enabled and the job is not suspended`) |
| `suspend: true` | Renders `spec.suspend: true` — see [Pausing](#pausing) |
| **A campaign whose rendered Job already exists in `rendered/` with a different volume list is rejected** | `render` prints `campaign <name> is append-only: create a new campaign` and exits non-zero — Job `completions` is immutable once created, so adding volumes means a new campaign file |
| **A pipeline whose image or steps changed while a rendered campaign still names it is rejected** | `validate` and `render` print `pipeline <id> changed (…) but campaigns …` and exit non-zero — see [Immutability](#immutability) |
| The file stem does not end in `-part<number>` | Validation error — that is what the converter calls the parts of a campaign it splits, so such a file would collide with one |
| More than 10 000 volumes, or more than 900 KiB of `volumes.txt` (an `images:` volume is ONE line of space-joined URLs) | Split into `<name>-part1`, `-part2`, … — one Job and one ConfigMap each. The API server refuses a ConfigMap over 1 MiB; the rest is margin |
| A campaign that splits and whose name is long | The name is cut short in the part names: a Job's name is also a label value and its pods' name prefix (`<job>-<index>`), and a DNS label stops at 63 characters. `rendered/` holds `<shortened>-partN.yaml` |

The campaign file stem becomes the value of the converter's `campaign` label
and, for a campaign that does not split, the Job name; a campaign that splits
is `<stem cut to 50 characters>-partN` instead, one Job per part. The exact
label keys are in the skeletons under
[`manifests/`](https://github.com/AI-Riksarkivet/htrflow-batch/tree/main/packages/converter/src/htrflow_converter/manifests).

## Pipeline file — `pipelines/<id>.yaml`

```yaml
# Image MUST be digest-pinned — a mutable tag would let results published
# under one id be produced by different code over time.
image: <registry>/htrflow-batch@sha256:<64 hex digits>

max_seconds: 3600          # optional: this recipe's per-volume wall-clock
                           # budget (the pod's activeDeadlineSeconds),
                           # overriding converter.yaml's

ttl_seconds_after_finished: 1209600   # optional: how long THIS pipeline's
                                      # finished campaign Jobs stay,
                                      # overriding converter.yaml's

steps:                     # htrflow pipeline steps, passed through verbatim
  - step: Segmentation
    settings:
      model: yolo
      model_settings:
        model: Riksarkivet/yolov9-regions-1
        revision: 0123456789abcdef0123456789abcdef01234567   # YOLO: top-level,
                                                             # required when the
                                                             # cluster sets
                                                             # requireModelRevision
  - step: TextRecognition
    settings:
      model: TrOCR
      model_settings:
        model: Riksarkivet/trocr-base-handwritten-hist-swe-2
        model_kwargs:
          revision: …          # Hugging Face models (TrOCR, Donut, DiT):
                               # under model_kwargs, forwarded to
                               # from_pretrained -- NOT top-level
```

Rules enforced by `parse_pipeline` — a broken pipeline is reported as a
validation error and blocks rendering for every campaign that uses it:

| Rule | Why |
|------|-----|
| Pipeline id is a DNS-1123 label (lowercase, `[a-z0-9.-]` interior, ≤63 chars) | It becomes the ConfigMap name `htr-pipeline-<id>` |
| `image:` matches `<repository>@sha256:<64 hex>` | Digest pin — provenance is recorded per volume in `manifest.json` |
| `max_seconds:`, when set, is a positive integer | It becomes `spec.template.spec.activeDeadlineSeconds` — the *pod's* deadline, so only the overrunning attempt is killed — for every campaign on this pipeline; unset falls back to `converter.yaml`. A sixty-page spread recipe and a single-page one do not want the same budget, and a budget the volume cannot meet costs `backoffLimitPerIndex` retries before the index is capped |
| `ttl_seconds_after_finished:`, when set, is a positive integer | It becomes the campaign Job's `ttlSecondsAfterFinished`. The Job is an inspection window, not the campaign's record — that is the campaign's ConfigMap, which has no TTL ([The record a campaign leaves](../how-it-works/campaigns.md#the-record-a-campaign-leaves)) — so this is only how long `completedIndexes` stays readable with `kubectl` |
| `steps:` is present and a list | Only the `steps:` document goes into the ConfigMap; no `Export` steps (the wrapper appends them — a pipeline with one fails the warm-up) |
| A step that loads a model (`settings.model` names the loader) has only `model`, `model_settings` and `generation_settings` under `settings` | htrflow passes `model_settings` merged with every other key under `settings` to the model, so a key beside `model_settings` overrides the same key inside it — `revision: null` there loads an unpinned model. The chart's model-revision policy refuses the same shape at admission |
| No key the pipeline file does not have (a `model_revision:` key included — the pin lives in `steps`) | A stray key is a typo or a leftover, and both are cheaper to hear about at `validate` than to wonder about later |

Two further rules are the **cluster's**, enforced by Kyverno at admission
and by the Kyverno CLI in the campaigns repo's CI, not by `validate`: the
image's repository must be one the release's `security.allowedImageRepos`
names, and — when `security.requireModelRevision` is on — every
`model_settings.model` must carry a 40-hex `revision:`, either top-level
under `model_settings` (YOLO) or under `model_settings.model_kwargs` (TrOCR
and other Hugging Face-backed models, whose loader forwards `model_kwargs`
straight to `from_pretrained`). Either placement satisfies the rule, but a
model only reads the one its own loader expects, so pinning it the wrong way
for that model still fails to load. Only **top-level** `steps:` are walked; a
step nested inside a conditional or composite construct is not. Both rules
are checked against everything the namespace admits, not only against what
this repo rendered.

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
| `priority:` naming a class `converter.yaml` does not list | `priority "htr-urgent" is not one of the cluster's classes (htr-interactive, htr-bulk, htr-idle) — set converter.yaml priority_classes to what the chart's queue.priorityClasses ships` |
| A campaign file called `foo-part1.yaml` | `the campaign name (taken from the file name) ends in "-part<number>", which is what the converter calls the parts of a campaign it splits — rename the file` |
| A tag instead of a digest in `image:` | `"image" is not pinned to a digest (got "repo/img:v5") — write image: <registry>/<repo>@sha256:<64 hex digits>` |
| `namespace:` written as anything but a DNS-1123 label | `"namespace" is not a Kubernetes namespace (got "htr.batch.example") — use lower-case letters, digits and "-", starting and ending with a letter or digit, at most 63 characters and no dots` |
| `queue:`, `s3_secret:`, `data_pvc:` or `runtime_class:` written as anything but a Kubernetes object name | `"queue" is not a Kubernetes object name (got "HTR-Batch") — use lower-case letters, digits, "-" and ".", starting and ending with a letter or digit, at most 253 characters` |
| A `node_selector:` key or value that is not a label | `"node_selector" has a key that is not a Kubernetes node label (got "Bad Key") — a key is a name, optionally after a "<dns-prefix>/"; both halves and the value are letters, digits, ".", "_" and "-", at most 63 characters` |
| `allowed_image_repos:` or `require_model_revision:` in `converter.yaml` | `allowed_image_repos moved to the htrflow-batch chart (security.allowedImageRepos, enforced by Kyverno) — remove it from converter.yaml` |
| A `source_template:` with no `{ref}` in it, with `{ref}` twice, or with any other placeholder | `"source_template" must have {ref} in it exactly once and nothing else in braces (got "https://iiif.example.org/{id}/manifest") — {ref} is where a campaign's bare volume id goes` — it is filled in for every bare volume id, so a template that cannot be filled would otherwise fail per volume |
| `steps:` that is not a list | `"steps" must be a list of steps — write steps: and then "- step: <Name>" entries under it` |
| `window: "5"` (quoted, so YAML makes it text), `window: 0`, `window: true` | `"window" must be a whole number of 1 or more (got "5" — quotes make it text)` — the "quotes" half is added only when the value really is a number, so `suspend: maybe` is not told about quotes it does not have |
| `max_seconds:` likewise | `"max_seconds" must be a whole number of seconds, 1 or more (got 0)` |
| `max_seconds:` or `ttl_seconds_after_finished:` larger than a 32-bit field | `"max_seconds" must be 2147483647 or less (got 4294967296)` — both are rendered into int32 Kubernetes fields, so a larger number is a 422 halfway through an apply |
| `manifest_max_bytes:` or `fetch_max_bytes:` at 0 | `"fetch_max_bytes" must be 1 or more (got 0)` — at 0 every image is over the cap, so every volume of every campaign fails |
| `suspend: maybe` (or any other non-boolean) | `"suspend" must be true or false (got "maybe")` |
| A setting given a list or a block where one value belongs | `"window" must be a whole number (got a list)` — the value is described, never dumped as a Python repr |
| A bad value inside a nested setting | `"node_selector.a" must be text (got 1)`, `"tolerations" entry 1 must be settings written as "key: value" lines (got 3)` — a list position is counted from 1, never shown as `tolerations.0` |
| A key the file (`converter.yaml` or a pipeline) does not have | `"bogus_field" is not a setting this file has — remove it, or fix the spelling` |
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
`main` (never hand-edited), and is what Argo CD or `htrflow-campaigns apply`
(`make campaigns-apply DIR=<campaigns-repo-dir>`) applies — pipelines first,
since a campaign's Job references its pipeline's ConfigMap.
`htrflow-campaigns apply` does that ordering itself; by hand it is
`kubectl apply -f rendered/pipelines -f rendered/campaigns`.

`--out` says where a render is *written*. What it is held against is always
the repo's own committed `rendered/`: that is the record of what has been
applied, so rendering into a fresh directory — or the temp directory an
`apply` with no `--out` uses — does not turn a campaign that is already
running into a new one.

`render` also **removes** files under `--out` that this render did not
produce, so deleting `campaigns/<name>.yaml` deletes
`rendered/campaigns/<name>.yaml` too. Deleting the manifest is only half of
cancelling: the apply has to prune as well, and **pruning is opt-in on both
sides**. Argo CD's `syncPolicy.automated.prune` defaults to `false` and a
manual sync does not prune unless asked, so an Application that manages this
repo needs `syncPolicy.automated.prune: true` (or `argocd app sync --prune`);
by hand it is `make campaigns-apply DIR=<campaigns-repo-dir> PRUNE=1`, which
runs `htrflow-campaigns apply --prune` — that lists every Job and ConfigMap
in the namespace carrying the converter's `managed-by=converter` label and
deletes the ones this render did not produce. Every object the converter
renders — both ConfigMaps and both Jobs — carries that label for exactly
this reason.

A render that produces **no campaigns at all** is refused with `--prune`
instead of cancelling every campaign in the namespace: an empty
`campaigns/`, a mistyped directory and a checkout that never happened all
look like that. Pass `--allow-empty` when retiring the last campaign really
is what you mean (`make campaigns-apply DIR=… PRUNE=1 ALLOW_EMPTY=1`).
`--dry-run` still prints what such a prune would delete, and says the real
run will refuse it.

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
client, server-side apply — there is no `kubectl` to install), and once the
objects are applied it patches `spec.active` on each campaign's Workload
(`false` for a suspended campaign, `true` otherwise), idempotently. It does
that before `--prune` deletes anything, and whatever the prune meets: an
object the prune may not delete is reported by name and counted as refused
(exit `3`), and a Job the TTL controller reaped first is simply gone. Deactivating a Workload
evicts its pods, keeps every finished index, and `kubectl get job` reports
`suspend: true`; reactivating continues at the next index. Results already in
S3 are never touched.

**A brand-new paused campaign has no Workload at the instant the apply
returns** — Kueue creates it a moment later, and that moment is exactly the
window in which Kueue would admit and start the campaign. Skipping it would
therefore *run* a campaign that git says is paused, so the apply waits
(`--pause-wait`, 10 × 1 s by default) for a paused campaign's Workload and
exits non-zero with a message if it never appears: re-run the apply. A
campaign that is *not* paused is still skipped when its Workload is missing —
a Workload that does not exist is not admitted either, and the next apply
catches it.

!!! warning "A campaign committed as `suspend: true` still runs one pod for a few seconds"

    Kueue creates, admits and unsuspends the Job in the same second it is
    created — before any apply-time step can look at it — so one pod starts
    and is deleted a few seconds later, mid-volume. Nothing is written for
    that volume (the index is simply retried when the campaign resumes) but
    it is not "no pod ever starts".

    The race-free alternative — rendering a paused campaign's Job **without**
    the `kueue.x-k8s.io/queue-name` label, so Kueue never sees it — does not
    work: it strands an admitted Workload that goes on holding the
    campaign's quota, starving every other campaign in the ClusterQueue, and
    Kueue's webhook then refuses to put the label back
    (`metadata.labels[kueue.x-k8s.io/queue-name]: field is immutable`) on the
    resuming apply.

**The apply also writes `active: true` for every campaign that is not
suspended.** So a Workload that Kueue deactivated on its own — a requeue
limit hit, `maximumExecutionTimeSeconds` exceeded — is re-admitted at the next
apply and the campaign resumes at the next unfinished index. Git is the truth
about what should be running; nothing on the cluster stays paused unless the
campaign file says so.

With Argo CD, run the same command as a `PostSync` hook so a merged
`suspend: true` takes effect on sync. **The manifest below is illustrative,
not a tested manifest**: the image digest, the ref and the checkout are
placeholders you have to fill in. No new image is needed: `uvx` installs the
converter from this repo at the pinned ref, and the pod needs a checkout of
the campaigns repo to render (the hook re-renders rather than trusting the
synced `rendered/`):

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

The hook fails the sync on any non-zero exit, so treat exit `3` — some
objects refused, everything else applied — as what it is: the sync did
change the cluster, and the summary line in the hook's log names what is
still to fix. Exit `1` outranks it: nothing reached the cluster at all, or a
campaign git says is paused is not actually paused — which can be true while
other objects *were* applied, so read the summary line rather than inferring
it from the code. See [refused objects](#when-the-api-server-refuses-an-object).

## When the API server refuses an object

`apply` sends each rendered object on its own, and **one refusal is one
object's problem**: it is named on stderr in a sentence, everything else is
still applied, and a summary line at the end lists what was left unchanged.

```
Job htr-warmup-demo: the pod template changed and a Job's pod template is immutable once the Job exists — a pipeline id is a permanent name for a recipe, so a changed recipe is a new pipeline file, and a Job that has to change is deleted and created again
1 of 6 objects were refused by the API server and are unchanged: Job/kyrk — the other 5 were applied (exit 3)
```

The codes are a precedence, highest first — `1` beats `3` beats `0` — so
`1` does not mean nothing was applied when a pause is what failed:

| Exit | What it means |
| --- | --- |
| `1` | a pause is **not enforced** — a paused campaign's Workload never appeared, or its Job was refused — whatever else was applied; or nothing reached the cluster at all (no credentials, an unreachable API server, a render that did not pass, a server that refused every object) |
| `3` | some objects were refused and are unchanged — or `--prune` could not delete some — everything else was applied, and every pause holds; the summary line names each of them |
| `0` | everything was applied |

A Job's **pod template cannot be edited** once the Job exists — that is
Kubernetes, not this tool — and two quite different changes move one: an
edit to the recipe, and an upgrade of the converter or a `converter.yaml`
setting that reaches every Job at once (the GPU RuntimeClass, the Hub
token's environment variable). They get different answers:

- **A warm-up Job is replaced.** It is idempotent — its completion marker
  sits on the model-cache volume, so a re-run is a file check — and it holds
  no campaign state, so `apply` deletes it (background propagation, then it
  waits for the deletion), creates it again, and says
  `replaced: Job/htr-warmup-<id> — its pod template changed …`. A warm-up
  that is **running** is left alone and reported instead: deleting it would
  take the pod that is downloading with it, while campaigns wait on its
  marker.
- **A campaign Job never is.** Its completed indexes and its results *are*
  the campaign, and deleting it would start every volume over. It is
  reported, left exactly as it is, and the apply exits 3. A pipeline edit
  under a live campaign is caught earlier than this, by `validate` — see
  [Immutability](#immutability).

## Immutability

A pipeline id is a **permanent name for a recipe**: changing the steps or the
image under an existing id is drift once results exist under it. To change a
recipe, mint a new id (`demo-v2`); old results under `demo-v1` stay untouched
and comparable side by side.

`validate` and `render` enforce that for a pipeline a campaign is still
running. `rendered/` is committed, so the previous render is the record:
they compare the image and the steps of each `pipelines/<id>.yaml` against
what `rendered/pipelines/<id>.yaml` holds, and refuse the edit when a
campaign that is still in `campaigns/` was already rendered against it —

```
pipeline demo-v1 changed (image) but campaigns kyrk, loc still run it — a pipeline is immutable while campaigns reference it; add a new pipeline file (demo-v1-2) and point new campaigns at it
```

Nothing renders, and the apply that would have met
`spec.template: field is immutable` halfway through never runs. A pipeline
no rendered campaign names may still be edited: a campaign whose file has
been removed (how a finished campaign is
[retired](../how-it-works/campaigns.md#removing-a-finished-campaign)) holds
nothing, and neither does one being rendered for the first time. The rest —
an id no campaign has ever used, and the discipline of not reusing one whose
results are published — stays a convention of the campaigns repo, whose
write access is part of the
[trust model](../how-it-works/security.md#trust-boundary).

Only the *recipe* is held immutable, not the rendered manifest: upgrading
the converter, or changing a `converter.yaml` setting, renders every warm-up
Job's pod template differently without changing a recipe by a word, and
`apply` [replaces the warm-up Job](#when-the-api-server-refuses-an-object)
for those.

A campaign, separately, is append-only at the volume-list level (see the
table above) — that one *is* enforced by `render`, because a running Job's
`completions` cannot change.
