# Campaign & Pipeline YAML

A campaigns git repo has three things the converter reads:
`converter.yaml` (cluster-wide defaults), `campaigns/*.yaml` (what to run)
and `pipelines/*.yaml` (how to run it). The filename stem is the campaign /
pipeline id. `htrflow-campaigns init <dir>` writes a repo in exactly this
shape — see [Run a Campaign](../getting-started/campaigns.md). The commands
that check, render and apply a repo are in [htrflow-campaigns CLI](cli.md).

Source: [`packages/converter/src/htrflow_converter/parse.py`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/packages/converter/src/htrflow_converter/parse.py),
[`models.py`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/packages/converter/src/htrflow_converter/models.py).

## `converter.yaml`

Cluster-wide defaults for everything the converter renders. Unknown keys are
rejected. Every field is optional; the values below are the defaults, except
`source_template`, which is shown as a placeholder. It has no default: set it
to your IIIF source before a campaign lists a volume as a bare reference code
(`- R0001203`). Without it, only `manifest:` and `images:` volumes are allowed.

In all three kinds of file, a key written twice in one mapping is a
validation error naming both lines (YAML itself would silently keep the
last).

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
tolerations: []                   # Kubernetes tolerations, each naming its taint's key (see below)
public_results_base: "https://<results-host>/<bucket>"   # required: the http(s) URL browsers read results at (the chart's publicResultsBase)
source_template: "https://<iiif-host>/<path>/{ref}/manifest"   # no default; manifest URL for a bare volume id, {ref} is the id
max_seconds: 21600                # each pod's activeDeadlineSeconds; a pipeline's own `max_seconds:` overrides it
warmup_wait_seconds: 900          # how long a pod waits for its pipeline's warm-up marker before failing the index; capped by that pod's own deadline
ttl_seconds_after_finished: 604800  # a week: how long a finished campaign's Job stays before Kubernetes deletes it; a pipeline's own `ttl_seconds_after_finished:` overrides it
manifest_max_bytes: 16777216      # 16 MiB
fetch_max_bytes: 67108864         # 64 MiB
image_cache: null                 # off by default; { bucket: <name> } caches source images in that S3 bucket
flavors: []                       # the chart's queue.flavors, by name and nodeLabels (see Pod sizes)
sizes: {}                         # named pod sizes a pipeline's `size:` picks (see Pod sizes)
default_size: null                # the size a pipeline with no `size:` runs at (see Pod sizes)
```

The file itself is required: every command refuses a repo without one,
rather than guess the namespace it applies and prunes in. So is
`public_results_base`, and it must be an http(s) URL a browser can open:
every campaign pod gets it, and without it every volume fails once the pod
has been admitted and has waited for its warm-up.

`queue`, `s3_secret`, `data_pvc`, `hf_token_secret` and
`priority_classes` name objects the chart creates or allows;
[Configuration](configuration.md) shows which chart value each must agree
with. `priority_classes` is checked here because the cluster will not check
it: Kueue leaves a Job naming a missing class "Queued" for ever
([Queueing](../how-it-works/queueing.md)). An empty list refuses every
`priority:`.

`tolerations` are copied into every warm-up and campaign pod, spelt the
Kubernetes way (`key`, `operator`, `value`, `effect`, `tolerationSeconds`).
Each must name its taint's `key`: a keyless toleration tolerates every
taint. A toleration for the control plane's taint
(`node-role.kubernetes.io/control-plane` or `…/master`) is refused too. On a
single-node cluster whose node is the control plane, take the taint off the
node instead:
`kubectl taint nodes <node> node-role.kubernetes.io/control-plane:NoSchedule-`.

`s3_secret`, `data_pvc` and `hf_token_secret` are checked for shape only.
Which Secrets and PVCs a pod may mount is the chart's admission policy.

`hf_token_secret` is needed only for a **private or gated** Hugging Face
model. It names a Secret you create, with a `token` key holding a
read-scope Hub token; only the warm-up Job gets it, as `HF_TOKEN`
([Deploy](../getting-started/deploy.md)).

`image_cache` is off by default, and absent renders every campaign pod
exactly as before it existed. Set `bucket` to cache source page images in an
S3 bucket on the same store as the results, so a volume run again needs
nothing from the IIIF server; the bucket is **private** (never in a public
policy, never linked) and must exist before a campaign runs
([Deploy](../getting-started/deploy.md)). Setting it renders every campaign
pod's `IMAGE_CACHE_BUCKET`. See [S3 Layout](s3-layout.md#image-cache-bucket)
for the key layout and what a hit or a miss does.

### Pod sizes

A pipeline asks for a pod size by name, and `converter.yaml` says what each
name means. A pipeline with no `size:` runs at `default_size`. With that
unset too, its campaign pods ask for 4 CPU, 8 GiB of memory with a 16 GiB
limit, 1 GPU and a 2 GiB `/work`, on the first flavor with room.

```yaml title="converter.yaml"
flavors:                          # the chart's queue.flavors, names and nodeLabels as there
  - name: small-gpu
    nodeLabels: { nvidia.com/gpu.product: <product label of the small card> }
  - name: large-gpu
    nodeLabels: { nvidia.com/gpu.product: <product label of the large card> }
sizes:
  small: { flavor: small-gpu, gpu: 1, cpu: 4, memory: 16Gi }
  large: { flavor: large-gpu, gpu: 1, cpu: 8, memory: 32Gi, workdir: 4Gi }
default_size: small               # pipelines without a size: the small card, not the first with room
```

| Key | Default | What it does |
|---|---|---|
| `flavor` | none | A `flavors` entry. Its `nodeLabels` become the pod's `nodeSelector`, which keeps Kueue off the other flavors (see the rule below). None: the first flavor with room |
| `gpu` | `1` | `nvidia.com/gpu`, 1 or more |
| `cpu` | required | Whole cores (`8`) or millicores (`500m`) |
| `memory` | required | Whole bytes, or with `Ki`, `Mi`, `Gi`, `Ti` (or `k`, `M`, `G`, `T`). Must leave at least 1Gi beside `workdir`: the in-memory `/work` counts against it, and the process gets the rest. The 1Gi is a floor against slips, not a sizing: a model wants several |
| `workdir` | `2Gi` | The size of the memory-backed `/work`, at least `512Mi`: it holds `HOME`, `TMPDIR` and the wrapper's page lookahead (`LOOKAHEAD_BYTES`), which is half of it |

Request and limit are the same number. A pod using more memory than it
requested is the first the kubelet evicts under pressure, and the pages
in `/work` would go with it.

Kueue has no way for a Job to ask for a flavor by name. It tries the
ClusterQueue's flavors in order and skips any whose node labels contradict
the pod's node selector, comparing only the keys that flavor names. So a
size's flavor is rendered as that flavor's labels, and **every two flavors
must name a label key in common, with different values**: give each the
same key (`nvidia.com/gpu.product`, say) with a value of its own
([Several sorts of GPU](../how-it-works/queueing.md#several-sorts-of-gpu)).
That is why `flavors` repeats the chart's `queue.flavors`, names and labels
alike, as `priority_classes` repeats its classes. Order does not matter
here; in the chart it does.

`validate` refuses flavors that break the rule, a size that names a flavor
not in `flavors`, a flavor whose labels contradict `node_selector`, a
`default_size` that is not a size, and a pipeline naming a size not in
`sizes`:

```
pipelines/demo-v1.yaml: size "huge" is not one of converter.yaml's sizes (small, large) — name one of them, or leave size out for the default
```

`validate` cannot see the cluster. `apply` can: before it sends a campaign
whose size names a flavor, it reads the ClusterQueue's flavors and holds the
campaign back when they are not `converter.yaml`'s
([Several sorts of GPU](../how-it-works/queueing.md#several-sorts-of-gpu)).

Setting or changing `default_size` changes the size of every pipeline
without one, and a running campaign's size cannot change: `validate` refuses
it like any other change to those pipelines.

What neither can see is the chart's quota. A size that asks for more
than every flavor it may land on has quota for is never admitted, and reads
"Queued" for ever: give each flavor a quota of at least one pod of the
largest size that can land on it.

!!! note "The image allow-list and the model-revision rule are cluster policy"

    They are Kyverno policies the chart ships (`security.allowedImageRepos`,
    `security.requireModelRevision`), not converter settings, because
    admission sees everything the namespace admits. A `converter.yaml` that
    carries `allowed_image_repos` or `require_model_revision` is a
    validation error. A campaigns repo's CI runs the same policies over
    `rendered/` with the Kyverno CLI. See
    [Security](../how-it-works/security.md#trust-boundary).

## Campaign file — `campaigns/<name>.yaml`

```yaml
pipeline: demo-v1          # required: a pipeline id from pipelines/
priority: ""                # optional: one of converter.yaml's priority_classes (htr-interactive, htr-bulk, htr-idle);
                            # orders the queue, never evicts a running campaign; empty is htr-bulk
window: 20                   # optional: this campaign's parallelism, clamped to converter.yaml's window
suspend: false               # optional: true pauses this campaign (see the CLI reference, "Pausing")
volumes:
  # 1) Bare string: a reference code at your IIIF source. The manifest URL
  #    is templated from converter.yaml's source_template, which must be set.
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

    Every `manifest:` and `images:` URL is stored verbatim: in git, in
    `rendered/`, in the campaign's ConfigMap and in each volume's
    `manifest.json`. A presigned URL publishes its signature to everyone who
    can read any of those. See
    [Source URLs are not secrets](../how-it-works/security.md#source-urls-are-not-secrets).

Rules enforced by `parse_campaign` (`validate`, and by `render`):

| Rule | When violated |
|------|---------------|
| `pipeline:` is required and names a file in `pipelines/` | Validation error; nothing renders |
| No unknown key on the campaign or on a volume | Validation error naming the key. A misspelt `suspended: true` would otherwise run the campaign unpaused |
| At least one volume | Validation error. A Job with `completions: 0` is Succeeded the moment it is created |
| Every volume is a bare string, or has `manifest:` or a non-empty `images:` | Validation error |
| `manifest:` and every `images:` entry are absolute `http(s)` URLs | Validation error (`must be an http(s) URL`) |
| …that a browser can open: no backslash or control character, a valid port, and a host that is a DNS name (IDN allowed), a dotted IPv4 or a bracketed IPv6 address | Validation error naming the volume. The viewer and the status page build links with the browser's URL parser, which is stricter than Python's |
| No whitespace inside a URL | Validation error; percent-encode a space as `%20`. Whitespace separates `images:` URLs in `volumes.txt`; a comma is fine |
| An `images:` volume's `volumes.txt` line is at most 100 KiB | Validation error. The Job exports that line as one env entry, and Linux caps one entry at 128 KiB. Split the volume, or give it a IIIF manifest |
| Volume ids match `[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?` | Validation error (`unsafe volume id`). This is the label-value alphabet, so uppercase is allowed |
| An id YAML would read as something else (`0012345` octal, `1:20` base 60, `1.10` a number, `yes`, `2024-01-31` a date) is quoted | Validation error saying what YAML read |
| Volume ids are unique within a campaign | Validation error (`duplicate volume id`) |
| `priority:`, when set, is one of `converter.yaml`'s `priority_classes` | Validation error naming the classes. Leaving it out is `htr-bulk`; a higher class goes ahead of waiting campaigns but never evicts a running one ([Queueing](../how-it-works/queueing.md)) |
| `window:`, when set, is a positive integer | Validation error |
| `window:` above `converter.yaml`'s `window` | Clamped to it at render time. Set that cap to what the ClusterQueue's quota can admit ([Queueing → The window](../how-it-works/queueing.md#the-window)) |
| A window change that would change a rendered, unpaused campaign's pod count | A `warning:`; `apply` refuses it for a running campaign ([CLI](cli.md#changing-a-running-campaigns-window)) |
| `suspend: true` | Renders `spec.suspend: true` ([CLI → Pausing](cli.md#pausing)) |
| **A rendered campaign's volume list is unchanged** | `campaign <name> is append-only: create a new campaign`, non-zero exit. `completions` cannot change on a Job |
| **A pipeline a rendered campaign still runs keeps its image and steps** | `pipeline <id> changed (…) but campaigns …`, non-zero exit ([Immutability](#immutability)) |
| The file stem is a DNS-1123 label (lower-case, digits, `-`, no dots, ≤63), does not start with `htr-warmup-`, and does not end in `-part<number>` or `-status` | Validation error. Pod hostnames must be DNS labels; the other names belong to warm-up Jobs, split parts and status records |
| The last pod name, `<job>-<completions − 1>`, is at most 63 characters | `campaign <name> cannot be applied: its last pod would be …`. A 61-character name takes at most 10 volumes |
| More than 10 000 volumes (so an index has at most four digits, which every pod name has room for), or more than 900 KiB of `volumes.txt` | Split into `<name>-part1`, `-part2`, …, one Job and ConfigMap each; the stem is cut to 50 characters. The API server refuses a ConfigMap over 1 MiB |
| Two campaigns that would split onto the same shortened stem | Refused, since their part files would collide |

Checks that need the live cluster (the campaign's live ConfigMap, a
running campaign's window, two campaigns sharing a volume) are made by
`apply` ([CLI → Checks](cli.md#checks-apply-makes-against-the-cluster)).

**A rule added later does not reach a campaign already rendered.** A
campaign whose volume list is exactly what the committed `rendered/`
recorded for it keeps that rendering, even where a rule added since would
refuse it: a volume id written unquoted that YAML reads as a number, a
source URL a browser cannot open, or a bare volume id with no
`source_template` set (it keeps the manifest URL it was rendered with). Its
list is append-only, so it could never be brought to pass. `validate` and
`render` print a `warning:` line instead, which says the id the volume was
rendered under (write it quoted, as that id), what the browser would refuse,
or that `source_template` should be set to the template the bare ids were
rendered with. A new campaign, or
a rendered one whose list changes, is held to every rule.

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

size: large                # optional: one of converter.yaml's sizes; none is
                           # 4 CPU, 8Gi (16Gi limit), 1 GPU, 2Gi /work

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
  - step: Segmentation     # the lines within each region: a reader needs
    settings:              # regions, then lines (see the rules below)
      model: yolo
      model_settings:
        model: Riksarkivet/yolov9-lines-within-regions-1
        revision: …
  - step: TextRecognition
    settings:
      model: TrOCR
      model_settings:
        model: Riksarkivet/trocr-base-handwritten-hist-swe-2
        model_kwargs:
          revision: …          # Hugging Face models (TrOCR, Donut, DiT):
                               # under model_kwargs, forwarded to
                               # from_pretrained -- NOT top-level
        processor_kwargs:
          revision: …          # TrOCR, WordLevelTrOCR, Donut, DiT: the
                               # processor is a second download, pinned
                               # on its own
```

Rules enforced by `parse_pipeline` — a broken pipeline is reported as a
validation error and blocks rendering for every campaign that uses it:

| Rule | Why |
|------|-----|
| Pipeline id is lowercase, `[a-z0-9.-]` inside, alphanumeric at both ends, ≤52 chars | It becomes `htr-pipeline-<id>` and `htr-warmup-<id>`, and a Job name is also a 63-character label value |
| `image:` matches `<repository>@sha256:<64 hex>` | The digest is the provenance recorded in every `manifest.json` and ALTO |
| `max_seconds:`, when set, is a positive integer | It becomes each pod's `activeDeadlineSeconds` for this pipeline's campaigns. A budget a volume cannot meet costs retries before the index fails |
| `ttl_seconds_after_finished:`, when set, is a positive integer | It becomes the campaign Job's `ttlSecondsAfterFinished`: how long `completedIndexes` stays readable with `kubectl`. The campaign's record has no TTL |
| `size:`, when set, is one of `converter.yaml`'s `sizes` | It becomes the wrapper's requests and limits, its `/work` and its flavor's node selector. Kueue would queue a pod it cannot place for ever |
| `steps:` is a non-empty list, each entry `- step: <Name>`, with no `Export` step | Otherwise every volume fails in the wrapper. The wrapper appends the Export steps itself |
| A step that loads a model has only `model`, `model_settings` and `generation_settings` under `settings` | htrflow merges any other key over `model_settings`, so `revision: null` beside it would unpin the model |
| A line reader (`model: TrOCR` or `PyLaia`) has at least two segmentation steps (`model: yolo` or `PPDocLayoutV3`) before it: regions, then the lines within them | ALTO and PAGE XML hold the text of a line only inside a region. Lines straight on the page are exported without their text (see below) |
| No unknown key (`model_revision:` included; the pin lives in `steps`) | A stray key is a typo or a leftover |

### Regions, then lines

Every model step runs on the smallest pieces the page has so far, whatever
the step is called. A segmentation step splits each piece into the regions
it finds, one level down. A line reader puts its text on the piece it
reads. htrflow's ALTO and PAGE export writes a text line only for a line
inside a region: page, region, line. A reader with one segmentation step
before it reads lines that sit directly on the page. The export then holds
none of their text: every page looks done and is empty. What the model
detects makes no difference. A lines model that runs straight on the page
still puts its lines one level below the page, not two.

So `validate` refuses that shape, and names the fix: a region segmentation
step (for example `Riksarkivet/yolov9-regions-1`) before the line step (for
example `Riksarkivet/yolov9-lines-within-regions-1`), as in the example
above. It also refuses a reader with no segmentation step before it.
`WordLevelTrOCR` is not held to the rule: the export writes its words.

A pipeline already in `rendered/` with exactly these steps and image is not
refused, since its id names that recipe for good (see
[Immutability](#immutability)). `validate` warns instead; new campaigns
should use a new pipeline file with the region step.

The wrapper checks the result as well, for shapes this rule cannot see. A
page whose ALTO or PAGE XML holds none of the text htrflow recognized for
it fails. If every page of a volume fails that way, the volume fails
permanently (see [the wrapper's stages](../how-it-works/wrapper.md#stages-around-the-streaming-loop)).

### Predicted page quality

```yaml
  - step: QualityPrediction
    settings:
      model_settings:
        model: <org>/<qp-model-repo>        # Hub repo id
        revision: <40-hex commit sha>
        model_file: <name>.joblib
        bin_config_file: <name>.json
      feature_groups: [segmentation, layout, htr_confidence, text, regionization]   # optional
```

A `QualityPrediction` step scores each page's transcription, so it is
refused wherever it sits before the pipeline's `TextRecognition` step, and
refused the same way when the pipeline has no `TextRecognition` step at
all. Its model is a pickle, so `model_settings.model` must be a Hugging
Face Hub repo id, never a local path, and `model_settings.revision` must be
the model's 40-hex commit sha. `model_settings.model_file` and
`model_settings.bin_config_file` must each be a plain file name inside
that repo — no path separator, and never `.` or `..`. The optional
`feature_groups` list may only name groups this image can compute from the
page tree alone (`segmentation`, `layout`, `htr_confidence`, `text`,
`regionization`); a group that needs the page image, a DiT model or a
language model this image does not run is refused by name, the same as a
group that does not exist at all. A pipeline may carry at most one
`QualityPrediction` step. Its settings are
part of the recipe like every other step's: changing them changes
`recipe_sha256`, so a pinned pipeline is never rewritten in place.

Two more rules are the **cluster's**, enforced by Kyverno at admission and
by the Kyverno CLI in the campaigns repo's CI:

- The image's repository is one `security.allowedImageRepos` names.
- With `security.requireModelRevision`, the model-revision policy holds
  every pipeline ConfigMap to these rules:

| Rule | Refused as |
|---|---|
| Every `model_settings.model` has a 40-hex `revision:` where its loader reads it: `model_settings.revision` for YOLO, `model_settings.model_kwargs.revision` for Hugging Face models (TrOCR, Donut, DiT) | `models not pinned to a revision: <models> — add revision: <40-character commit hash> under model_settings (YOLO) or model_settings.model_kwargs …` |
| TrOCR, WordLevelTrOCR, Donut and DiT also pin `model_settings.processor_kwargs.revision`: the processor is a second download | `processors not pinned to a revision: <models> — …` |
| No key beside `model_settings` in a step that loads a model | `settings beside model_settings in a step that loads a model: … — move them under model_settings` |
| The pipeline is under `data`, never `binaryData` | `a pipeline may not be carried in binaryData, where the revision rule cannot read it …` |

Either revision placement satisfies the policy, but a model reads only the
one its own loader expects, so a pin in the wrong place still fails to
load.

Only top-level `steps:` are walked. The wrapper holds the same three paths
when it loads a pipeline, and fails the volume permanently when a key
beside `model_settings` would load another revision than the one pinned.

## When something is wrong

`validate` and `render` print one line per problem, then a count, and
render nothing if there is any problem. Both hold a repo to the same rules,
so a pull request `validate` passes is one `render` on `main` takes. Every
line is `path/to/file.yaml: <what is wrong> — <what to write instead>`.

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
| Bare volume ids (`- R1`) with no `source_template:` in `converter.yaml` (one line per campaign) | `volumes "R1", "R2" and "R3" are bare reference codes, and converter.yaml has no source_template to turn them into manifest URLs — set source_template in converter.yaml (e.g. "https://iiif.example.org/{ref}/manifest"), or write each volume as "id:" with "manifest: <url>"` |
| A `source_template:` with no `{ref}` in it, with `{ref}` twice, or with any other placeholder | `"source_template" must have {ref} in it exactly once and nothing else in braces (got "https://iiif.example.org/{id}/manifest") — {ref} is where a campaign's bare volume id goes` |
| A reader with fewer than two segmentation steps before it | `"steps" put the lines that TrOCR (step 2) reads directly on the page, with 1 Segmentation step before it — htrflow's ALTO and PAGE export writes only the text of lines inside a region, so every page would publish without its text; put a region Segmentation step before the line step …` |
| `steps:` that is not a list | `"steps" must be a list of steps — write steps: and then "- step: <Name>" entries under it` |
| `window: "5"` (quoted, so YAML makes it text), `window: 0`, `window: true` | `"window" must be a whole number of 1 or more (got "5" — quotes make it text)` |
| `max_seconds:` likewise | `"max_seconds" must be a whole number of seconds, 1 or more (got 0)` |
| `max_seconds:` or `ttl_seconds_after_finished:` larger than a 32-bit field | `"max_seconds" must be 2147483647 or less (got 4294967296)` — both are int32 Kubernetes fields |
| `manifest_max_bytes:` or `fetch_max_bytes:` at 0 | `"fetch_max_bytes" must be 1 or more (got 0)` |
| `suspend: maybe` (or any other non-boolean) | `"suspend" must be true or false (got "maybe")` |
| A setting given a list or a block where one value belongs | `"window" must be a whole number (got a list)` |
| A bad value inside a nested setting | `"node_selector.a" must be text (got 1)`, `"tolerations" entry 1 must be settings written as "key: value" lines (got 3)` |
| A key the file (`converter.yaml` or a pipeline) does not have | `"bogus_field" is not a setting this file has — remove it, or fix the spelling` |
| A required key left out | `"image" is missing — add "image:" to this file` |
| Broken indentation or quoting | `this file is not valid YAML — <the line and column PyYAML names>` |
| A file that is a list, or free text | `this file must be campaign settings written as "key: value" lines — a bare list or a piece of text is not one` |

A campaign whose pipeline file is itself broken is not also told its
pipeline is missing. The wording is pinned in
`packages/converter/tests/test_parse.py` (`EXPECTED`).

## Immutability

A pipeline id is a **permanent name for a recipe**: changing the steps, the
image or the size under an existing id is drift once results exist under it. To change a
recipe, mint a new id (`demo-v2`); old results under `demo-v1` stay untouched
and comparable side by side.

`validate` and `render` enforce this while a campaign still runs the
pipeline. They compare each `pipelines/<id>.yaml` with the committed
`rendered/pipelines/<id>.yaml`, and refuse an edit to the image, steps or
size when a campaign still in `campaigns/` was rendered against it:

```
pipeline demo-v1 changed (image) but campaigns kyrk, loc still run it — a pipeline is immutable while campaigns reference it; add a new pipeline file (demo-v1-2) and point new campaigns at it
```

`apply` holds the same rule against the cluster, in case `rendered/` is
missing or behind: `pipeline demo-v1 is in the cluster with different steps
and campaigns kyrk, loc still run it … — nothing was applied`
([CLI → Checks](cli.md#append-only-and-immutable-recipes)).

Once no rendered campaign names the pipeline (a finished campaign's file is
[removed](../how-it-works/campaigns.md#removing-a-finished-campaign)), the
guard lets go. Not reusing an id whose results are published is then a
convention enforced by review.

Only the recipe is held immutable, not the rendered manifest. A converter
upgrade or a `converter.yaml` change that alters every warm-up Job's pod
template makes `apply`
[replace the warm-up Job](cli.md#when-the-api-server-refuses-an-object). What a size means in `converter.yaml` is not the recipe
either: changing it changes the pod template of every campaign Job at that
size, and the API server refuses that for a Job that exists, so `apply`
reports it and leaves the Job as it was
([CLI](cli.md#when-the-api-server-refuses-an-object)). New numbers belong
under a new size name. The record names the size, not its numbers: the
pipeline's ConfigMap carries the size's name, and the numbers that ran are
the ones in `converter.yaml` at the commit that rendered it, which the
campaign's Job in `rendered/` shows.

A campaign is append-only: `render` holds its volume list against
`rendered/`, and `apply` against the campaign's live ConfigMap, because a
Job's `completions` cannot change. New volumes go in a new campaign file.
