# A worked example: rendering an Indexed Job

This page runs `htrflow-campaigns render` for real on a tiny two-volume
campaign and walks the output field by field, so "[what the converter
renders](campaigns.md#what-the-converter-renders)" has a concrete example
next to it. Every block below is copied verbatim from an actual render —
nothing here is hand-typed.

## The inputs

Copied from [`examples/campaigns/`](https://github.com/AI-Riksarkivet/htrflow-batch/tree/main/examples/campaigns)
into a scratch directory, with one change: the campaign file below (two
volumes — one bare reference, one `images:` volume with two URLs) replaces
the example repo's single-volume `campaigns/demo.yaml`. `converter.yaml` and
`pipelines/demo-v1.yaml` are the example repo's own files, unmodified.

```yaml title="campaigns/trolldomskommissionen.yaml"
pipeline: demo-v1
volumes:
  - R0001203

  - id: loose-scans
    images:
      - https://example.org/scan1.jpg
      - https://example.org/scan2.jpg
```

```yaml title="pipelines/demo-v1.yaml"
image: docker.io/riksarkivet/htrflow-batch@sha256:cb30d020a943326bc484830362ee817c9a6734d6149915ca99571a0355bdcf89
steps:
  - step: Segmentation
    settings:
      model: yolo
      model_settings:
        model: Riksarkivet/yolov9-regions-1
  - step: TextRecognition
    settings:
      model: TrOCR
      model_settings:
        model: Riksarkivet/trocr-base-handwritten-hist-swe-2
```

`converter.yaml` sets the values that show up below: `namespace: htr-batch`,
`queue: htr-batch`, `window: 20`, `s3_secret: htr-batch-s3`,
`data_pvc: htr-test-data`, `runtime_class: nvidia`,
`public_results_base: "http://localhost:30900/htr-results"`, and the
defaults for everything it does not set (`max_seconds: 21600`,
`warmup_wait_seconds: 900`, `manifest_max_bytes`/`fetch_max_bytes` at
16/64 MiB). The full file and its field-by-field meaning:
[Campaign & Pipeline YAML](../reference/campaign-yaml.md#converteryaml).

## The command

```console
$ uv run --no-sync htrflow-campaigns render <scratch-dir> --out <scratch-dir>/rendered
```

`render` exits 0 and writes two files: `rendered/campaigns/trolldomskommissionen.yaml`
(the campaign's ConfigMap + Job) and `rendered/pipelines/demo-v1.yaml` (the
pipeline's ConfigMap + warm-up Job) — the same split as the inputs, one file
per source file.

## The campaign ConfigMap

```yaml title="rendered/campaigns/trolldomskommissionen.yaml (part 1 of 2)"
apiVersion: v1
kind: ConfigMap
metadata:
  name: campaign-trolldomskommissionen
  namespace: htr-batch
  labels:
    htrflow.riksarkivet.se/managed-by: converter
    htrflow.riksarkivet.se/campaign: trolldomskommissionen
    htrflow.riksarkivet.se/pipeline: demo-v1
data:
  volumes.txt: |
    R0001203	https://lbiiif.riksarkivet.se/arkis!R0001203/manifest
    loose-scans	images:https://example.org/scan1.jpg,https://example.org/scan2.jpg
```

Two lines, one per volume, in campaign-file order — the order that fixes
which line `$JOB_COMPLETION_INDEX` reads (index 0 gets `R0001203`, index 1
gets `loose-scans`; see [volumes.txt](../getting-started/run-a-volume.md#where-a-volume-comes-from-volumestxt)
for the exact format). The bare reference expanded through `converter.yaml`'s
`source_template` into a full manifest URL; the `images:` volume became one
line reading `images:` followed by its URLs, comma-joined, with no manifest
anywhere. `managed-by: converter` is what `apply --prune` and Argo CD's own
prune use to find this object again once its campaign file is deleted;
`campaign` and `pipeline` name where it came from.

## The Indexed Job

```yaml title="rendered/campaigns/trolldomskommissionen.yaml (part 2 of 2, trimmed)"
apiVersion: batch/v1
kind: Job
metadata:
  name: trolldomskommissionen
  namespace: htr-batch
  labels:
    app: htrflow-batch
    htrflow.riksarkivet.se/managed-by: converter
    htrflow.riksarkivet.se/campaign: trolldomskommissionen
    htrflow.riksarkivet.se/pipeline: demo-v1
    kueue.x-k8s.io/queue-name: htr-batch
spec:
  completionMode: Indexed
  completions: 2
  parallelism: 20
  backoffLimitPerIndex: 3
  maxFailedIndexes: 2
  podFailurePolicy:
    rules:
    - action: Ignore
      onPodConditions:
      - type: DisruptionTarget
    - action: FailIndex
      onExitCodes:
        containerName: wrapper
        operator: In
        values:
        - 13
    - action: FailIndex
      onExitCodes:
        containerName: warmup-wait
        operator: In
        values:
        - 13
  ttlSecondsAfterFinished: 86400
  template:
    spec:
      restartPolicy: Never
      activeDeadlineSeconds: 21600
      terminationGracePeriodSeconds: 120
      containers:
      - name: wrapper
        image: docker.io/riksarkivet/htrflow-batch@sha256:cb30d020a943326bc484830362ee817c9a6734d6149915ca99571a0355bdcf89
        args:
        - |
          set -eu
          mkdir -p "$HOME" "$TMPDIR" "$YOLO_CONFIG_DIR"
          line=$(sed -n "$((JOB_COMPLETION_INDEX + 1))p" /campaign/volumes.txt)
          [ -n "$line" ] || { echo "no volume for index $JOB_COMPLETION_INDEX" >&2; exit 13; }
          id=${line%%	*}; src=${line#*	}
          export VOLUME_REF="$id"
          case "$src" in images:*) export IMAGES="${src#images:}" ;; *) export IIIF_MANIFEST_URL="$src" ;; esac
          exec python -m htrflow_batch
        env:
        - name: PIPELINE_ID
          value: demo-v1
        - name: S3_PREFIX
          value: htr-batch/
        - name: IMAGE_DIGEST
          value: docker.io/riksarkivet/htrflow-batch@sha256:cb30d020a943326bc484830362ee817c9a6734d6149915ca99571a0355bdcf89
        - name: HF_HUB_OFFLINE
          value: '1'
        - name: HF_HOME
          value: /data/hf
        - name: HOME
          value: /work/home
        - name: TMPDIR
          value: /work/tmp
        volumeMounts:
        - name: campaign
          mountPath: /campaign
          readOnly: true
        - name: data
          mountPath: /data
          readOnly: true
        - name: work
          mountPath: /work
      volumes:
      - name: work
        emptyDir:
          medium: Memory
          sizeLimit: 2Gi
      initContainers:
      - name: warmup-wait
        image: docker.io/riksarkivet/htrflow-batch@sha256:cb30d020a943326bc484830362ee817c9a6734d6149915ca99571a0355bdcf89
        command:
        - /bin/sh
        - -c
        - 'n=0; until [ -f /data/warmup/demo-v1.done ]; do n=$((n+10)); [ "$n" -le
          900 ] || { echo "no warm-up marker at /data/warmup/demo-v1.done after 900s:
          the pipeline''s warm-up Job has not finished" >&2; exit 13; }; sleep 10;
          done'
      runtimeClassName: nvidia
```

What each field is for, and where its value came from:

- **`completionMode: Indexed`, `completions: 2`** — one index per volume,
  set once from the campaign's volume count (`campaigns/trolldomskommissionen.yaml`
  has two). Kubernetes cannot change `completions` on a running Job, which is
  the whole reason a campaign is append-only.
- **`parallelism: 20`** — `min(campaign window, converter.yaml window)`. This
  campaign sets no `window:` of its own, so it gets `converter.yaml`'s
  `window: 20` outright (the per-cluster GPU-quota cap); with only two
  volumes here, both indexes could run at once regardless.
- **`backoffLimitPerIndex: 3` / `maxFailedIndexes: 2`** — each index gets up
  to 3 retries (a converter constant, not a campaign or `converter.yaml`
  setting); the whole Job gives up once every index has exhausted its
  retries (`maxFailedIndexes` = `completions`, always).
- **`podFailurePolicy` rules, in order** — a `DisruptionTarget` condition
  (node drain, preemption) is `Ignore`d and burns no retry; a `wrapper` exit
  13 (`FailIndex`) is the wrapper's own "do not retry" signal (bad manifest,
  bad pipeline config); a `warmup-wait` exit 13 (`FailIndex`) means the
  init container gave up waiting for the marker — retrying only holds the
  GPU again for a marker that is not coming. Order matters: `Ignore` has to
  stay first or a drained pod would be charged as a failed attempt.
- **`kueue.x-k8s.io/queue-name: htr-batch`** — from `converter.yaml`'s
  `queue:`; this is the label Kueue's admission webhook watches to suspend
  the Job on creation and admit it as quota frees.
- **`warmup-wait` init container** — blocks on `/data/warmup/demo-v1.done`
  (`<pipeline id>.done`), polling every 10 s, for at most
  `min(warmup_wait_seconds, activeDeadlineSeconds - 10)` = `min(900, 21590)`
  = 900 s here (`converter.yaml`'s `warmup_wait_seconds` default, clamped
  under the pod's own deadline so the gate always expires before the
  kubelet would kill the pod itself). Past that bound it prints the marker
  path and exits 13, which the `podFailurePolicy` rule above turns into
  `FailIndex` — no retry buys a marker that is not coming.
- **`wrapper`'s env** — `PIPELINE_ID` and `S3_PREFIX` (`<namespace>/`,
  so `htr-batch/`) namespace the S3 keys under `converter.yaml`'s
  `namespace:`; `IMAGE_DIGEST` is the pipeline's own `image:` pin, restamped
  into every ALTO's provenance block; `HF_HUB_OFFLINE=1` and
  `HF_HOME=/data/hf` are how the wrapper finds the pre-warmed cache without
  ever trying to reach Hugging Face itself (see
  [The model cache](wrapper.md#the-model-cache)).
- **The shell prologue** — reads line `$JOB_COMPLETION_INDEX + 1` of
  `/campaign/volumes.txt` (1-indexed for `sed`, 0-indexed for Kubernetes),
  splits it on the tab into `id`/`src`, and exports `VOLUME_REF` plus either
  `IIIF_MANIFEST_URL` or `IMAGES` depending on whether `src` starts with
  `images:` — the same branch a hand-run wrapper takes, just written by the
  shell instead of by a person.
- **`activeDeadlineSeconds: 21600`** — `converter.yaml`'s `max_seconds`
  (this pipeline sets none of its own); the pod's own per-volume wall-clock
  budget, enforced by the kubelet, not by the wrapper.
- **`work` emptyDir, `medium: Memory`, `2Gi`** — the tmpfs workdir
  (`HOME`/`TMPDIR`/`YOLO_CONFIG_DIR` all point into it): where the wrapper
  downloads its lookahead window of pages, and the only place besides
  `HF_HOME` the read-only-rootfs container may write. Its size, not the
  volume's page count, is what actually bounds tmpfs (`LOOKAHEAD_PAGES`
  caps concurrency, not bytes).
- **`data` mount, `readOnly: true`** — the model cache PVC
  (`converter.yaml`'s `data_pvc: htr-test-data`), mounted read-only on every
  batch pod. The warm-up Job is the only writer; see
  [The model cache](wrapper.md#the-model-cache) for the whole contract.

## The pipeline ConfigMap

```yaml title="rendered/pipelines/demo-v1.yaml (part 1 of 2)"
apiVersion: v1
kind: ConfigMap
metadata:
  name: htr-pipeline-demo-v1
  namespace: htr-batch
  labels:
    htrflow.riksarkivet.se/managed-by: converter
    htrflow.riksarkivet.se/pipeline: demo-v1
  annotations:
    htrflow.riksarkivet.se/pipeline-sha256: 76e5b909b42fad30eafba857f40a4a5795d7b0c5952903457e0f99cb92da0b3f
data:
  pipeline.yaml: |
    steps:
    - step: Segmentation
      settings:
        model: yolo
        model_settings:
          model: Riksarkivet/yolov9-regions-1
    - step: TextRecognition
      settings:
        model: TrOCR
        model_settings:
          model: Riksarkivet/trocr-base-handwritten-hist-swe-2
```

Only the `steps:` document goes in — not `image:`, which lives in the Job
specs instead — because `steps:` is what htrflow itself parses
(`Pipeline.from_config`). The sha256 annotation is computed from exactly
that YAML dump and is the ground truth an operator can compare the
wrapper's own recorded `pipeline_sha256` against by hand; it changes only
if the `steps:` content changes, which — by the immutability convention —
should never happen under the id `demo-v1` once anything has run.

## The warm-up Job

```yaml title="rendered/pipelines/demo-v1.yaml (part 2 of 2, trimmed)"
apiVersion: batch/v1
kind: Job
metadata:
  name: htr-warmup-demo-v1
  namespace: htr-batch
  labels:
    app: htrflow-warmup
    htrflow.riksarkivet.se/managed-by: converter
    htrflow.riksarkivet.se/pipeline: demo-v1
spec:
  backoffLimit: 2
  podFailurePolicy:
    rules:
    - action: Ignore
      onPodConditions:
      - type: DisruptionTarget
    - action: FailJob
      onExitCodes:
        containerName: warmup
        operator: In
        values:
        - 13
  template:
    spec:
      restartPolicy: Never
      activeDeadlineSeconds: 3600
      containers:
      - name: warmup
        image: docker.io/riksarkivet/htrflow-batch@sha256:cb30d020a943326bc484830362ee817c9a6734d6149915ca99571a0355bdcf89
        args:
        - |
          set -eu
          mkdir -p "$HOME" "$TMPDIR" "$YOLO_CONFIG_DIR" "$HF_HOME"
          exec python -m htrflow_batch.warmup
        env:
        - name: PIPELINE_ID
          value: demo-v1
        - name: HF_HOME
          value: /data/hf
        volumeMounts:
        - name: data
          mountPath: /data
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: htr-test-data
      runtimeClassName: nvidia
```

One warm-up Job per pipeline id, rendered the first time that id appears in
`pipelines/` — this campaign's own render produced it as a side effect of
naming `pipeline: demo-v1`, not because the campaign file asked for it
directly. No Kueue queue label (it runs outside Kueue, on CPU:
`CUDA_VISIBLE_DEVICES: ""`), no `completionMode: Indexed` (it is a plain,
single-completion Job), and — unlike the campaign Job above — its `data`
mount carries no `readOnly: true`: this is the one pod in the whole system
allowed to write into the model cache. `backoffLimit: 2` plus the
`podFailurePolicy` give it its own, much smaller retry budget than a batch
index's. The full contract — what gets downloaded, who waits on it, what
happens on a cache miss — is [The model cache](wrapper.md#the-model-cache).

## Cleaning up

Nothing here is meant to be kept: `rendered/` in a real campaigns repo is
committed by CI, never produced by hand, and the scratch copy used for this
page was deleted afterwards. Running the same render again reproduces
byte-identical output — the converter is a pure function of the three input
files plus `converter.yaml`.
