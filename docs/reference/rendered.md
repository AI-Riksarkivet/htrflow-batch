# Rendered objects

This page runs `htrflow-campaigns render` on a small two-volume campaign and
walks through the output field by field. The blocks are abridged from a
real render (the full objects are the converter's golden files,
`packages/converter/tests/golden/`). Three kinds of value are replaced with
placeholders: the image digest, the manifest URL and the results base.
`<label-domain>` stands for the converter's label domain
(`_MANAGED_BY_LABEL` in `render.py`).

How the command applies these objects is in
[htrflow-campaigns CLI](cli.md). Why a campaign is shaped this way is in
[Campaigns](../how-it-works/campaigns.md).

## The inputs

These are the files from
[`examples/campaigns/`](https://github.com/AI-Riksarkivet/htrflow-batch/tree/main/examples/campaigns),
except for this campaign file:

```yaml title="campaigns/example.yaml"
pipeline: demo-v1
volumes:
  - id: volume-1
    manifest: <iiif-manifest-url>

  - id: loose-scans
    images:
      - https://example.org/scan1.jpg
      - https://example.org/scan2.jpg
```

```yaml title="pipelines/demo-v1.yaml"
image: <registry>/htrflow-batch@sha256:<digest>
steps:
  - step: Segmentation
    settings:
      model: yolo
      model_settings:
        model: Riksarkivet/yolov9-regions-1
  - step: Segmentation
    settings:
      model: yolo
      model_settings:
        model: Riksarkivet/yolov9-lines-within-regions-1
  - step: TextRecognition
    settings:
      model: TrOCR
      model_settings:
        model: Riksarkivet/trocr-base-handwritten-hist-swe-2
```

These values from `converter.yaml` show up below:

- `namespace: htr-batch`
- `queue: htr-batch`
- `window: 20`
- `s3_secret: htr-batch-s3`
- `data_pvc: htr-test-data`
- `runtime_class: nvidia`
- `public_results_base: <results-base-url>`

It sets nothing else, so the rest are the defaults:

- `max_seconds: 21600`
- `warmup_wait_seconds: 900`
- `ttl_seconds_after_finished: 604800`
- `manifest_max_bytes` and `fetch_max_bytes` at 16 and 64 MiB

The full file and what each field means are in
[Campaign & Pipeline YAML](campaign-yaml.md).

## The command

```console
$ uv run --no-sync htrflow-campaigns render <campaigns-repo> --out <campaigns-repo>/rendered
```

`render` exits 0 and writes one output file per source file:

- `rendered/campaigns/example.yaml`: the campaign's ConfigMap and Job.
- `rendered/pipelines/demo-v1.yaml`: the pipeline's ConfigMap and warm-up
  Job.

Running the same render again produces byte-identical output. The converter
is a pure function of its input files.

## The campaign ConfigMap

```yaml title="rendered/campaigns/example.yaml (part 1 of 2)"
apiVersion: v1
kind: ConfigMap
metadata:
  name: campaign-example
  namespace: htr-batch
  labels:
    <label-domain>/managed-by: converter
    <label-domain>/campaign: example
    <label-domain>/pipeline: demo-v1
  annotations:
    argocd.argoproj.io/hook: Skip
data:
  volumes.txt: |
    volume-1	<iiif-manifest-url>
    loose-scans	images:https://example.org/scan1.jpg https://example.org/scan2.jpg
```

There are two lines, one per volume, in campaign-file order. That order fixes
which line `$JOB_COMPLETION_INDEX` reads: index 0 gets `volume-1` and index 1
gets `loose-scans`. The exact format is in the
[Wrapper reference](wrapper.md).

- **A shorthand ref** would appear here already expanded into a full manifest
  URL through `converter.yaml`'s `source_template`.
- **The `images:` volume** becomes one line: `images:` followed by its URLs,
  space-joined, with no manifest anywhere.
- **`managed-by: converter`** is how `apply --prune` finds this object again
  once its campaign file is deleted.
- **`argocd.argoproj.io/hook: Skip`** keeps Argo CD from applying it: only
  `htrflow-campaigns apply` does.
- **`campaign` and `pipeline`** record where the object came from.

## The Indexed Job

```yaml title="rendered/campaigns/example.yaml (part 2 of 2, trimmed)"
apiVersion: batch/v1
kind: Job
metadata:
  name: example
  namespace: htr-batch
  labels:
    app: htrflow-batch
    <label-domain>/managed-by: converter
    <label-domain>/campaign: example
    <label-domain>/pipeline: demo-v1
    kueue.x-k8s.io/queue-name: htr-batch
  annotations:
    argocd.argoproj.io/hook: Skip
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
  ttlSecondsAfterFinished: 604800
  template:
    spec:
      restartPolicy: Never
      activeDeadlineSeconds: 21600
      terminationGracePeriodSeconds: 120
      automountServiceAccountToken: false
      containers:
      - name: wrapper
        image: <registry>/htrflow-batch@sha256:<digest>
        command: [/bin/sh, -c]
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
        - name: PIPELINE_PATH
          value: /config/pipeline.yaml
        - name: PIPELINE_ID
          value: demo-v1
        - name: S3_PREFIX
          value: htr-batch/
        - name: PUBLIC_RESULTS_BASE
          value: <results-base-url>
        - name: IMAGE_DIGEST
          value: <registry>/htrflow-batch@sha256:<digest>
        - name: HF_HUB_OFFLINE
          value: '1'
        - name: INDEX_FAILURE_COUNT
          valueFrom:
            fieldRef:
              fieldPath: metadata.annotations['batch.kubernetes.io/job-index-failure-count']
        - name: BACKOFF_LIMIT_PER_INDEX
          value: '3'
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
        - name: pipeline
          mountPath: /config
        - name: data
          mountPath: /data
          readOnly: true
          subPath: demo-v1-<recipe sha256>
        - name: work
          mountPath: /work
        - name: s3
          mountPath: /secrets/s3
          readOnly: true
        resources:
          requests: {cpu: "4", memory: 8Gi, nvidia.com/gpu: "1"}
          limits: {cpu: "4", memory: 16Gi, nvidia.com/gpu: "1"}
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: htr-test-data
      - name: work
        emptyDir:
          medium: Memory
          sizeLimit: 2Gi
      initContainers:
      - name: warmup-wait
        image: <registry>/htrflow-batch@sha256:<digest>
        command:
        - /bin/sh
        - -c
        - 'n=0; until [ -f /data/warmup/demo-v1.done ]; do n=$((n+10)); [ "$n" -le
          900 ] || { echo "no warm-up marker at /data/warmup/demo-v1.done after 900s:
          the pipeline''s warm-up Job has not finished" >&2; exit 13; }; sleep 10;
          done'
        terminationMessagePolicy: FallbackToLogsOnError
      runtimeClassName: nvidia
```

What each field is for, and where its value comes from:

- **`completionMode: Indexed`, `completions: 2`.** One index per volume, set
  once from the campaign's volume count. Kubernetes cannot change
  `completions` on an existing Job, which is why a campaign is append-only.
- **`parallelism: 20`.** This is `min(campaign window, converter.yaml window)`.
  The campaign sets no `window:` of its own, so it gets `converter.yaml`'s
  cap directly. With only two volumes, at most two pods run at once anyway.
  Against the default one-GPU quota, this campaign could never be admitted
  ([Queueing → The window](../how-it-works/queueing.md#the-window)).
- **`backoffLimitPerIndex: 3`, `maxFailedIndexes: 2`.** Each index gets up to
  3 retries, a converter constant. `maxFailedIndexes` always equals
  `completions`, so the campaign keeps going until every index has its own
  verdict.
- **`podFailurePolicy` rules, in order.**
  1. A `DisruptionTarget` condition (node drain, preemption) is ignored and
     costs no retry.
  2. A `wrapper` exit 13 is the wrapper's "do not retry" signal, for
     example a bad manifest or a bad pipeline config.
  3. A `warmup-wait` exit 13 means the init container gave up waiting for
     the marker. A retry would only hold the GPU again for a marker that is
     not coming.

  Order matters: `Ignore` must stay first, or a drained pod would be
  charged as a failed attempt.
- **`kueue.x-k8s.io/queue-name: htr-batch`.** Comes from `converter.yaml`'s
  `queue:`. Kueue's webhook suspends a Job with this label at creation, and
  Kueue admits it when quota frees.
- **The `warmup-wait` init container.** Waits for
  `/data/warmup/demo-v1.done` (`<pipeline id>.done`, inside this recipe's
  directory of the cache, see the `data` mount below), polling every 10 s for
  at most `min(warmup_wait_seconds, activeDeadlineSeconds - 10)`. Here that is
  `min(900, 21590)` = 900 s. The clamp makes the gate always expire before
  the kubelet would kill the pod. Past that bound, the gate prints the marker
  path and exits 13, which the rule above turns into `FailIndex`.
  `FallbackToLogsOnError` makes that line the pod's termination message.
- **The wrapper's env.**
  - `PIPELINE_ID` and `S3_PREFIX` (`<namespace>/`, here `htr-batch/`) place
    the S3 keys.
  - `IMAGE_DIGEST` is the pipeline's own `image:` pin, stamped into every
    ALTO's provenance block.
  - `INDEX_FAILURE_COUNT` (the Job controller's
    `batch.kubernetes.io/job-index-failure-count` annotation on the pod,
    through the downward API) and `BACKOFF_LIMIT_PER_INDEX` (copied from
    `spec.backoffLimitPerIndex`) tell the wrapper which attempt of its index
    it is on, so it can fail a page it still defers on the last one.
  - `HF_HUB_OFFLINE=1` and `HF_HOME=/data/hf` let the wrapper find the
    pre-warmed cache without ever contacting Hugging Face
    ([The model cache](../how-it-works/wrapper.md#the-model-cache)).
- **The shell prologue.** Reads line `$JOB_COMPLETION_INDEX + 1` of
  `/campaign/volumes.txt` (`sed` counts from 1, Kubernetes indexes from 0),
  splits it on the tab into `id` and `src`, and exports `VOLUME_REF`. It then
  exports `IMAGES` if `src` starts with `images:`, and `IIIF_MANIFEST_URL`
  otherwise.
- **`activeDeadlineSeconds: 21600`.** The pod's per-volume wall-clock
  budget, from `converter.yaml`'s `max_seconds`, because this pipeline sets
  none. The kubelet enforces it, not the wrapper. It is on the pod, not the
  Job, so only the overrunning attempt is killed.
- **`terminationGracePeriodSeconds: 120`.** Room for the wrapper's final
  run-log ship (a 90 s budget) after a SIGTERM
  ([Failure Handling](../how-it-works/failure-handling.md)).
- **`ttlSecondsAfterFinished: 604800`.** How long the finished Job stays
  readable with `kubectl`: `converter.yaml`'s default, or the pipeline's
  own. The campaign's record outlives it
  ([Campaigns → The record](../how-it-works/campaigns.md#the-record-a-campaign-leaves)).
- **`automountServiceAccountToken: false`.** A campaign pod never talks to
  the Kubernetes API.
- **The `work` emptyDir (`medium: Memory`, `2Gi`).** The tmpfs workdir.
  `HOME`, `TMPDIR` and `YOLO_CONFIG_DIR` all point into it. The wrapper
  downloads its lookahead window of pages here, and apart from the tmpfs this
  read-only-rootfs container can write nowhere
  ([The Wrapper → Memory bounds](../how-it-works/wrapper.md#memory-bounds)).
- **The `data` mount, `readOnly: true`, and its `subPath`.** The model
  cache PVC (`converter.yaml`'s `data_pvc`), mounted read-only on every batch
  pod, and only this pipeline's directory on it: `<pipeline id>-<recipe
  sha256>`, a digest of the steps and the image together. The init container
  mounts the same directory, so the marker it waits for is this recipe's.
  The warm-up Job of this pipeline is that directory's only writer.

## The pipeline ConfigMap

```yaml title="rendered/pipelines/demo-v1.yaml (part 1 of 2)"
apiVersion: v1
kind: ConfigMap
metadata:
  name: htr-pipeline-demo-v1
  namespace: htr-batch
  labels:
    <label-domain>/managed-by: converter
    <label-domain>/pipeline: demo-v1
  annotations:
    argocd.argoproj.io/hook: Skip
    <label-domain>/pipeline-sha256: <sha256 of pipeline.yaml>
data:
  pipeline.yaml: |
    steps:
    - step: Segmentation
      settings:
        model: yolo
        model_settings:
          model: Riksarkivet/yolov9-regions-1
    - step: Segmentation
      settings:
        model: yolo
        model_settings:
          model: Riksarkivet/yolov9-lines-within-regions-1
    - step: TextRecognition
      settings:
        model: TrOCR
        model_settings:
          model: Riksarkivet/trocr-base-handwritten-hist-swe-2
```

Only the `steps:` document goes in, not `image:`. `steps:` is what htrflow
parses (`Pipeline.from_config`), and the image lives in the Job specs. The
sha256 annotation is computed from exactly this YAML dump. It changes only
when `steps:` changes, which should never happen under the id `demo-v1` once
anything has run.

## The warm-up Job

```yaml title="rendered/pipelines/demo-v1.yaml (part 2 of 2, trimmed)"
apiVersion: batch/v1
kind: Job
metadata:
  name: htr-warmup-demo-v1
  namespace: htr-batch
  labels:
    app: htrflow-warmup
    <label-domain>/managed-by: converter
    <label-domain>/pipeline: demo-v1
  annotations:
    argocd.argoproj.io/hook: Skip
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
    metadata:
      annotations:
        <label-domain>/recipe-sha256: <recipe sha256>
    spec:
      restartPolicy: Never
      activeDeadlineSeconds: 3600
      containers:
      - name: warmup
        image: <registry>/htrflow-batch@sha256:<digest>
        args:
        - |
          set -eu
          mkdir -p "$HOME" "$TMPDIR" "$YOLO_CONFIG_DIR" "$HF_HOME"
          exec python -m htrflow_batch.warmup
        env:
        - name: PIPELINE_ID
          value: demo-v1
        - name: CUDA_VISIBLE_DEVICES
          value: ""
        - name: HF_HOME
          value: /data/hf
        volumeMounts:
        - name: data
          mountPath: /data
          subPath: demo-v1-<recipe sha256>
        resources:
          requests: {cpu: "2", memory: 4Gi}
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: htr-test-data
      runtimeClassName: nvidia
```

There is one warm-up Job per pipeline id. The campaign file did not ask for
it: it exists because `pipelines/demo-v1.yaml` exists. A pruning apply
deletes it, together with the pipeline ConfigMap, once that file is gone. How
it differs from the campaign Job:

- **No Kueue queue label.** It runs outside Kueue, on CPU
  (`CUDA_VISIBLE_DEVICES: ""`, no GPU request).
- **Not an Indexed Job.** It is a plain Job with a single completion.
- **Its `data` mount has no `readOnly: true`.** It is the one pod allowed to
  write into this recipe's directory of the model cache, and it sees no other
  directory. A warm-up runs its author's model code (a YOLO `.pt` file is a
  pickle), so no pipeline's warm-up can reach what another pipeline's
  campaigns load offline.
- **A recipe change is a new directory, and a new warm-up.** The directory
  and the pod template's `recipe-sha256` annotation both follow the digest
  of the steps and the image. Change either, on a pipeline no rendered
  campaign runs, and the apply replaces the warm-up Job. Its campaigns then
  wait for the marker in the new, empty directory, instead of finding the
  old recipe's marker and running offline without the new model. The price
  is disk: two pipelines, or two recipes of one, that load the same model
  each keep a copy of it, since isolating them is the point.
- **A smaller retry budget.** `backoffLimit: 2`, with a `podFailurePolicy`
  that fails the whole Job on exit 13.
- **The same `runtimeClassName`, `nodeSelector` and `tolerations`** as the
  campaign Job, so it lands where the cache PVC is mounted.
