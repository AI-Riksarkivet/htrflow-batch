# Troubleshooting

Symptom first, then what to run, then the fix. `<namespace>` is the release
namespace, `<campaign>` a campaign Job's name, `<results-base-url>` the
release's `publicResultsBase`. Why each mechanism behaves as it does is in
[How it Works](../how-it-works/architecture.md).

## Quick lookups

| Question | Command |
|---|---|
| Is anything running? | `kubectl -n <namespace> get jobs,workloads` |
| Which volume is on the GPU right now? | `kubectl -n <namespace> get pods -L batch.kubernetes.io/job-completion-index` |
| How far has this campaign got? | `kubectl -n <namespace> get job <campaign> -o jsonpath='{.status.completedIndexes} {.status.failedIndexes}'` |
| Why did an index fail? | `kubectl -n <namespace> get pods -l batch.kubernetes.io/job-name=<campaign> -o jsonpath='{.items[*].status.containerStatuses[*].state.terminated.message}'` |
| …and the pod is already gone? | `curl <results-base-url>/status/logs/<pipeline>/<volume>.txt` |
| How far is this volume right now? | `curl <results-base-url>/<namespace>/<pipeline>/<volume>/progress.json` |
| Is this volume actually finished? | `curl -I <results-base-url>/<namespace>/<pipeline>/<volume>/manifest.json` (only `manifest.json` means done) |
| Which image and models produced this ALTO? | Read the file: its `Processing ID="htrflow-batch"` block names them |

## A campaign reads "Queued" and nothing is admitted

"Queued" is not a Kueue state: the campaign browser shows it for a Job that
is suspended and has no finished volume yet. That covers a campaign waiting
its turn, one paused in git before its first volume finished, and one that
can never be admitted.

**Check**

```bash
kubectl -n <namespace> get workloads                 # ADMITTED empty = still waiting
kubectl -n <namespace> describe workload <workload>
kubectl get clusterqueue <queue>-cq -o yaml          # pendingWorkloads, flavorsUsage
kubectl -n <namespace> get events --sort-by=.lastTimestamp
kubectl -n kueue-system get pods                     # is Kueue itself running?
```

To find a Job's Workload:

```bash
JOB_UID=$(kubectl -n <namespace> get job <campaign> -o jsonpath='{.metadata.uid}')
kubectl -n <namespace> get workloads -l "kueue.x-k8s.io/job-uid=$JOB_UID"
```

**Fix**, by cause:

| What you see | Cause | Fix |
|---|---|---|
| Workloads pending while the GPU is idle | The Kueue controller is down; from outside it looks like a busy GPU | Check the Kueue controller before the GPU |
| Other campaigns hold `flavorsUsage` at the quota | It is waiting its turn | Nothing, or raise `queue.resources` ([Deploy → Queue quota](deploy.md#queue-quota)) |
| Pending while the queue is empty | The Job's `parallelism × per-pod requests` exceeds the quota, so it is inadmissible for ever | Set `converter.yaml`'s `window` so that `window × per-pod requests` fits the quota, then re-render ([Queueing](../how-it-works/queueing.md)) |
| A Job but no Workload at all, and no event | Its `priority:` names a `WorkloadPriorityClass` the cluster does not have | Match `converter.yaml`'s `priority_classes` to the chart's `queue.priorityClasses`; `validate` then refuses the bad name |
| Workload `Evicted`, reason `Deactivated` | The campaign is paused in git | Remove `suspend: true` from the campaign file and apply |

## Pods stuck in `Init:0/1`, or volumes failing on a missing marker

Every campaign pod waits in its `warmup-wait` init container for its
pipeline's warm-up marker, for at most `warmup_wait_seconds` (default 900).
Past that it exits 13 and the index fails without retry.

**Check**, in this order:

```bash
# 1. The index that gave up names the marker it waited for.
kubectl -n <namespace> get pods -l batch.kubernetes.io/job-name=<campaign> \
  -L batch.kubernetes.io/job-completion-index
kubectl -n <namespace> logs <pod> -c warmup-wait
# no warm-up marker at /data/warmup/<pipeline>.done after 900s: …

# 2. The warm-up Job says why it never wrote one.
kubectl -n <namespace> get job htr-warmup-<pipeline>
kubectl -n <namespace> logs job/htr-warmup-<pipeline> --tail=50
kubectl -n <namespace> get pods -l batch.kubernetes.io/job-name=htr-warmup-<pipeline> \
  -o jsonpath='{.items[*].status.containerStatuses[*].state.terminated.message}'

# 3. What is on disk, from a running pod of the same pipeline.
kubectl -n <namespace> exec <running-pod> -- ls -l /data/warmup
```

The warm-up's message also shows on the campaign card's warm-up chip.

**Fix**, by what the warm-up Job shows:

| Warm-up Job | Cause | Fix |
|---|---|---|
| Still running | A slow first download | Wait. Its pod deadline is 1 h, and a timeout is retried |
| `Failed`, exit 13 | A bad model id or revision, an unknown step, a setting a step does not take, invalid YAML | Fix the pipeline (under a new pipeline id if campaigns already ran it) and apply |
| `Failed`, transient (a missing HF token Secret, a Hub outage) | Its retries are spent | Fix the cause and re-run the apply. **The apply replaces a failed warm-up by itself**; do not delete it by hand |
| Pod never starts, `CreateContainerConfigError` | `hf_token_secret` names a Secret that does not exist | Create it ([Deploy → Hugging Face token](deploy.md#hugging-face-token-for-a-private-model)) |
| `Complete`, but no marker where the campaign looks | The two pods are not looking at the same volume, or the marker was removed | See below |

A `Complete` warm-up with no marker: first check that the warm-up Job has
the same `runtimeClassName`, `nodeSelector` and `tolerations` as the
campaign Job. With two or more GPU nodes, a `ReadWriteOnce` cache can be
filled on one node and read on another; use a `ReadWriteMany` class
([The Wrapper → The model cache](../how-it-works/wrapper.md#the-model-cache)).
Once fixed, delete the Complete Job by hand, since an unchanged apply leaves
a Complete warm-up alone:

```bash
kubectl -n <namespace> delete job htr-warmup-<pipeline>
```

then re-run the apply. Volumes whose index already failed on the missing
marker do not run again in that campaign; see
[Re-run a failed volume](#re-run-a-failed-volume).

## A volume failed

The campaign card gives one sentence per failed volume, and its run log is
one click away on the same row.

| The card says | What to do |
|---|---|
| Stopped when its time budget ran out | Nothing: the next attempt resumes. If it keeps happening, raise `max_seconds` (in `converter.yaml`, or on a new campaign) for the campaigns that follow |
| Stopped by the cluster (a node drain or a pause) | Nothing: the index is retried |
| Settings incomplete or wrong, a deployment problem | Fix `converter.yaml` or the chart values and re-render. The campaign file is fine |
| The IIIF manifest could not be read, will not be retried | Fix the URL, then put the volume in a new campaign |
| N pages are missing, retried automatically | Nothing, unless the retries also fail. Only the missing pages are redone |
| None of the pages processed produced a result | Look at the node and the pipeline before the retries run out |
| Stopped without a message this page can read | Open the run log |

A volume whose state is `failed` has spent its retries, and the card says
so; nothing will run it again in that campaign.

## Re-run a failed volume

1. Fix the cause: a model, a manifest URL, a pipeline bug.
2. Add the volume to a **new** campaign file. The old campaign cannot be
   edited: campaigns are append-only, and removing the volume from it is
   refused too.

On the same pipeline id, the apply holds the new campaign back while a
campaign that shares the volume is still running, since both would write
the same results; it says so and starts once the old one finishes. Under a
new pipeline id it starts at once.

## The campaign browser shows no progress for a running volume

The read API reads each running volume's `progress.json` from the bucket
itself. When `publicResultsBase` does not resolve from inside the cluster,
that read fails silently.

**Check**: `curl` the volume's `progress.json` from your machine. If it
answers there but the card shows nothing, the pod cannot reach that address.

**Fix**: set `web.internalResultsBase` to an in-cluster address of the
bucket ([View results](viewing.md#exposing-the-web-front)).

## Logs or ALTO views are refused

The run viewer and `/alto` only read addresses under the chart's
`publicResultsBase`. Runs published under a different base (a changed
chart value, or a `converter.yaml` `public_results_base` that does not
match) are refused. Make the two equal
([View results](viewing.md#exposing-the-web-front)).

## The apply exits non-zero

`1` is a pause that is not enforced, nothing applied, or a campaign record
that could not be read; `3` is some objects refused with every pause
holding. The message on stderr names each object. What each refusal means
and what to do is in [htrflow-campaigns CLI](../reference/cli.md).

## The model cache

There is no API for the cache, only the filesystem. A campaign pod mounts
only its own recipe's directory, read-only. To see or change the whole
cache, start a debug pod that mounts the PVC (on a `ReadWriteOnce` volume it
must land on the node that holds it; pin it with `nodeName` if needed):

```bash
kubectl run htr-cache-debug -n <namespace> --rm -it --restart=Never --image=busybox \
  --overrides='{"spec":{"containers":[{"name":"debug","image":"busybox","command":["sh"],"stdin":true,"tty":true,"volumeMounts":[{"name":"data","mountPath":"/data"}]}],"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"<cache-pvc>"}}]}}'
```

Each recipe is a directory `/data/<pipeline-id>-<recipe sha256>/`. A
pipeline's current recipe hash:

```bash
kubectl -n <namespace> get job htr-warmup-<pipeline-id> \
  -o jsonpath='{.spec.template.metadata.annotations.htrflow\.riksarkivet\.se/recipe-sha256}'
```

| Task | Command |
|---|---|
| How full the cache is, per recipe | `du -sh /data/*/hf` |
| Cached model snapshots | `find /data/*/hf/hub -maxdepth 1 -name 'models--*'` |
| Which recipes are warmed | `ls /data/*/warmup` |
| Force a re-warm | Delete the marker (`rm /data/<pipeline-id>-<recipe sha256>/warmup/<pipeline-id>.done`) **and** the Complete Job (`kubectl -n <namespace> delete job htr-warmup-<pipeline-id>`), then apply |
| Free space | Delete a retired pipeline's directory; nothing prunes the cache |

After replacing the cache PVC, delete every `htr-warmup-*` Job and apply,
so they warm the new volume. A model missing from the cache during a run
fails the attempt as transient; retries succeed once the cache is fixed.
